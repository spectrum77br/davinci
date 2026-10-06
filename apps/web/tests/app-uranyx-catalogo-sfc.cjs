// Run from apps/web: node tests/app-uranyx-catalogo-sfc.cjs
// App Uranyx (06/10/2026): o módulo do app dos clientes no DaVinci. Contrato
// em app-uranyx/docs/integracao/conteudo-e-catalogo-v1.md, seção 6.
//
// O que este teste trava:
//  · todas as telas e componentes do módulo compilam (parse + compileTemplate);
//  · só admin com `app_uranyx: true` no /me vê o menu e entra nas páginas
//    (middleware `admin` + `app-uranyx`), como o Atendimento;
//  · as chamadas vão para /api/app-uranyx/<mesmo caminho do /admin>, e o 503
//    app_uranyx_indisponivel vira a tela "API do app fora do ar ou não
//    configurada" (nos dois formatos de erro: o da API do app e o do DaVinci);
//  · o Catálogo carrega, filtra e roda "Sincronizar com o site" (POST) com o
//    relatório; a gaveta do produto manda no PATCH só o que mudou, e a foto
//    nova como foto_arquivo_id;
//  · o selo do relatório: cópia que nunca rodou é "ainda não rodou", não "falhou";
//  · erro de rede (sem conexão, tempo esgotado, proxy sem a API) vira frase em
//    português, também no envio da foto; os erros de negócio da API continuam;
//  · Contas: `?conta=` sai da URL ao fechar e quando a conta não existe mais;
//  · os helpers puros (código no caminho, foto reduzida a 1600 px, listas,
//    tags, YouTube, pacote Android, documento);
//  · "Não fui eu": "Quem pediu" mostra os campos reais do requerente (a lista
//    dos pedidos comprovados como lista, a data formatada), é o dado para
//    aceitar o pedido (que EXCLUI a conta atual);
//  · relatório da cópia: avisos, produtos inválidos e a conferência no DaVinci
//    que não rodou aparecem e contam como pendência (nunca "Nada para conferir");
//  · receita com eletrodoméstico que saiu da categoria: rótulo certo e o PATCH
//    só leva a lista quando a seleção mudou, já sem ele (sem o 422);
//  · alteração (não-GET) com a API do app fora/sem resposta: o erro fica na
//    ação, a tela não troca; tempo esgotado nunca diz "nada foi alterado";
//  · SSR: "Não fui eu" e Contas (com `?conta=`) só chamam a API no navegador.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// `~/lib/...` resolve para as libs já carregadas aqui (o alias do Nuxt não existe no node).
const ALIAS = {}
const requerer = (m) => (m in ALIAS ? ALIAS[m] : require(m))

function loadLib(rel, globais = {}) {
  const exp = {}
  const nomes = Object.keys(globais)
  new Function('exports', 'require', ...nomes, transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(
    exp, requerer, ...nomes.map((n) => globais[n]),
  )
  return exp
}

function sfc(rel) {
  const filename = path.join(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: `${path.basename(rel)}-check` })
  assert.deepEqual(compiled.errors, [], `${rel}: template`)
  new Function('exports', 'require', transpile(compiled.code))({}, require)
  return descriptor
}

const tick = () => new Promise((r) => setTimeout(r, 0))

// As frases do 503 app_uranyx_indisponivel vêm da API do DaVinci: lidas do
// próprio repasse.py, para a tela não perder a frase do tempo esgotado se ela mudar.
const REPASSE_PY = fs.readFileSync(path.join(__dirname, '../../api/app/services/app_uranyx/repasse.py'), 'utf8')
function msgDoRepasse(nome) {
  const m = REPASSE_PY.match(new RegExp(`^${nome} = (\\([\\s\\S]*?\\n\\)|"[^"\\n]*")`, 'm'))
  assert.ok(m, `${nome} em repasse.py`)
  return [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]).join('')
}
const MSG_DEMOROU = msgDoRepasse('MSG_DEMOROU')
const MSG_FORA_DO_AR = msgDoRepasse('MSG_FORA_DO_AR')

// Requerente do "Não fui eu" no formato de servicos/cadastro.py (reivindicar). FALSO.
const REQUERENTE = {
  email: 'quem.pediu@exemplo.test',
  celular: '11911111111',
  pedidos_provados: ['260901ABC123', '260915XYZ789'],
  instalacao_id: 'inst-falsa-1',
  plataforma: 'android',
  integridade: 'basico',
  em: '2026-10-06T15:00:00+00:00',
  outros_casos_abertos: 1,
}

// ---------------------------------------------------------------- todos os SFCs do módulo
const PAGINAS = ['catalogo', 'excecoes', 'receitas', 'apps', 'contas', 'fila']
const COMPONENTES = fs.readdirSync(path.join(__dirname, '../components')).filter((f) => f.startsWith('AppUranyx'))
assert.ok(COMPONENTES.length >= 10, 'componentes do módulo presentes')
const descritores = {}
for (const p of [...PAGINAS, 'index']) descritores[p] = sfc(`../pages/app-uranyx/${p}.vue`)
for (const c of COMPONENTES) descritores[c] = sfc(`../components/${c}`)

// Toda página do módulo: admin + app-uranyx (a mesma trava do menu e da API).
for (const p of PAGINAS) {
  const src = descritores[p].scriptSetup.content
  const chamadas = src.match(/definePageMeta\(([\s\S]*?)\)\s*\n/g) || []
  assert.equal(chamadas.length, 1, `${p}: um definePageMeta só`)
  let meta
  new Function('definePageMeta', chamadas[0])((m) => { meta = m })
  assert.deepEqual(meta, { middleware: ['admin', 'app-uranyx'] }, `${p}: middleware admin + app-uranyx`)
  assert.match(descritores[p].template.content, /<RouteTabs :tabs="TABS_APP_URANYX" \/>/, `${p}: abas do módulo`)
  assert.match(descritores[p].template.content, /<AppUranyxIndisponivel v-if="indisponivel"/, `${p}: tela de API fora do ar`)
  // Nada de armazenamento do navegador nem de CPF na URL.
  assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(src), `${p}: sem armazenamento do navegador`)
}
{
  let meta
  const src = descritores.index.scriptSetup.content
  new Function('definePageMeta', src)((m) => { meta = m })
  assert.deepEqual(meta, { redirect: '/app-uranyx/catalogo' }, '/app-uranyx abre no Catálogo')
}
// O documento vai no corpo (nunca na URL) na busca de contas.
assert.match(descritores.contas.scriptSetup.content, /chamar<Conta\[\]>\('contas\/busca', \{ method: 'POST', body: \{ documento: docLimpo\.value \} \}\)/)

// ---------------------------------------------------------------- lib/appUranyx.ts
const L = loadLib('../lib/appUranyx.ts')
ALIAS['~/lib/appUranyx'] = L
{
  // Abas: só admin, e cada uma tem a página.
  assert.deepEqual(L.TABS_APP_URANYX.map((t) => t.to), PAGINAS.map((p) => `/app-uranyx/${p}`))
  assert.ok(L.TABS_APP_URANYX.every((t) => t.adminOnly === true), 'abas só para admin')

  // Erro no formato da API do app (repassado pelo DaVinci).
  const v = L.erroAppUranyx({ status: 422, data: { erro: 'validacao', mensagem: 'Confira os dados e tente de novo.', campos: { titulo: 'Muito curto', 'ingredientes.2': 'Muito longo' } } })
  assert.equal(v.codigo, 'validacao')
  assert.equal(v.texto, 'Confira os dados e tente de novo.')
  assert.deepEqual(L.linhasDoErro(v), ['Título: Muito curto', 'Ingredientes (item 3): Muito longo'])
  assert.equal(v.indisponivel, false)

  // 503 nos dois formatos: vira a tela de "fora do ar" (nas leituras) e o
  // texto é a frase da API, que diz o porquê; sem frase, o título.
  for (const [e, texto] of [
    [{ status: 503, data: { erro: 'app_uranyx_indisponivel', mensagem: 'A API do app não respondeu.' } }, 'A API do app não respondeu.'],
    [{ status: 503, data: { detail: { code: 'app_uranyx_indisponivel', message: 'APP_URANYX_API_URL vazio' } } }, 'APP_URANYX_API_URL vazio'],
    [{ status: 503, data: { detail: { code: 'app_uranyx_indisponivel', message: MSG_FORA_DO_AR } } }, MSG_FORA_DO_AR],
    [{ status: 503, data: { detail: { code: 'app_uranyx_indisponivel' } } }, 'API do app fora do ar ou não configurada'],
  ]) {
    const er = L.erroAppUranyx(e)
    assert.equal(er.indisponivel, true)
    assert.equal(er.texto, texto)
    assert.equal(er.demorou, false)
  }
  // Sem resposta a tempo entre o DaVinci e a API do app: a frase do repasse
  // (confira antes de repetir), nunca "nada foi alterado".
  {
    assert.match(MSG_DEMOROU, /não respondeu a tempo/)
    const er = L.erroAppUranyx({ status: 503, data: { detail: { code: 'app_uranyx_indisponivel', message: MSG_DEMOROU } } }, 'Não deu para salvar')
    assert.equal(er.indisponivel, true)
    assert.equal(er.demorou, true)
    assert.equal(er.texto, MSG_DEMOROU)
    assert.ok(!/nada foi alterado/i.test(er.texto))
    assert.ok(L.semRespostaATempo(MSG_DEMOROU))
    assert.ok(!L.semRespostaATempo(MSG_FORA_DO_AR))
    assert.ok(!L.semRespostaATempo(null))
  }
  // 503 sem o código (proxy caído) NÃO é "não configurada": é erro do servidor.
  const semCodigo = L.erroAppUranyx({ status: 503, data: null }, 'Não deu para carregar')
  assert.equal(semCodigo.indisponivel, false)
  assert.match(semCodigo.texto, /^Não deu para carregar: o servidor respondeu com erro \(503\)/)

  // Trava do DaVinci (formato {detail: {code}}).
  assert.match(L.erroAppUranyx({ status: 403, data: { detail: { code: 'app_uranyx_restrito' } } }).texto, /liberado para o App Uranyx/)
  // Mensagem da API do app manda (409 de "já ligado a outro produto").
  assert.equal(
    L.erroAppUranyx({ status: 409, data: { erro: 'conflito', mensagem: 'O código dg053 já está ligado a A17. Desligue lá antes.' } }).texto,
    'O código dg053 já está ligado a A17. Desligue lá antes.',
  )
  assert.match(L.erroAppUranyx({ status: 422, data: { erro: 'arquivo_invalido' } }).texto, /JPG, PNG ou WebP até 5 MB/)
  // 422 do próprio DaVinci (Pydantic).
  const p = L.erroAppUranyx({ status: 422, data: { detail: [{ loc: ['body', 'documento'], msg: 'Field required' }] } })
  assert.deepEqual(p.campos, { documento: 'Field required' })
  // Rota que ainda não subiu (404 seco do FastAPI), sem conexão, sessão.
  assert.match(L.erroAppUranyx({ status: 404, data: { detail: 'Not Found' } }).texto, /ainda não tem esta parte/)
  assert.match(L.erroAppUranyx({ message: '[GET] "/api/app-uranyx/catalogo": <no response> Failed to fetch' }, 'X').texto, /^X: sem conexão/)
  assert.match(L.erroAppUranyx({ status: 401, data: {} }).texto, /sessão do DaVinci/)
  // 401 da API do app (token da equipe errado) mostra a frase dela.
  assert.equal(L.erroAppUranyx({ status: 401, data: { erro: 'nao_autenticado', mensagem: 'Acesso ao painel negado.' } }).texto, 'Acesso ao painel negado.')

  // Rede: nunca o texto técnico cru.
  assert.match(L.erroAppUranyx(new TypeError('Failed to fetch'), 'X').texto, /^X: sem conexão com o servidor/)
  const tempo = Object.assign(new Error('[GET] "/api/app-uranyx/catalogo": <no response> The operation was aborted due to timeout'), { name: 'FetchError' })
  assert.equal(L.erroAppUranyx(tempo, 'X').texto, 'X: o servidor demorou demais para responder. Tente de novo.')
  assert.equal(L.erroAppUranyx(tempo).demorou, true)
  assert.match(L.erroAppUranyx(Object.assign(new Error('x'), { cause: { name: 'TimeoutError' } }), 'X').texto, /demorou demais/)
  // Alteração sem resposta no navegador: pode ter sido feita, nada de "tente de novo" às cegas.
  for (const e of [
    Object.assign(new Error('[POST] "/api/app-uranyx/receitas": <no response> The operation was aborted due to timeout'), { name: 'FetchError' }),
    Object.assign(new Error('x'), { cause: { name: 'TimeoutError' }, options: { method: 'patch' } }),
  ]) {
    const er = L.erroAppUranyx(e, 'X')
    assert.equal(er.texto, 'X: o servidor demorou demais para responder e a alteração pode ter sido feita. Confira antes de repetir.')
    assert.equal(er.demorou, true)
  }
  assert.equal(L.erroAppUranyx(new TypeError('Failed to fetch')).demorou, false)
  // Proxy do Nuxt sem a API atrás: o `message` em inglês do corpo não aparece.
  for (const [st, msg] of [[502, 'Bad Gateway'], [503, 'Dev server is unavailable.']]) {
    const er = L.erroAppUranyx({ status: st, data: { error: true, statusCode: st, statusMessage: msg, message: msg } }, 'X')
    assert.equal(er.texto, `X: o servidor respondeu com erro (${st}). Tente de novo em instantes.`)
  }
  // Erro de negócio continua passando: a `message` do DaVinci (no `detail`) e a `mensagem` da API do app.
  assert.equal(L.erroAppUranyx({ status: 413, data: { detail: { code: 'app_uranyx_arquivo_grande', message: 'O arquivo passa de 25 MB.' } } }).texto, 'O arquivo passa de 25 MB.')
  assert.equal(L.erroAppUranyx({ status: 404, data: { erro: 'nao_encontrado', mensagem: 'Conta não encontrada.' } }).texto, 'Conta não encontrada.')

  // Código-base no caminho (pode ter espaço, acento, barra).
  assert.equal(L.codigoNoCaminho('dg300/azul'), 'dg300%2Fazul')
  assert.equal(L.codigoNoCaminho('kit promo 01'), 'kit%20promo%2001')
  assert.equal(L.codigoNoCaminho('ação1'), 'a%C3%A7%C3%A3o1')

  // Foto: lado maior até 1600 px, sem aumentar.
  assert.deepEqual(L.dimensoesReduzidas(4000, 3000), { largura: 1600, altura: 1200 })
  assert.deepEqual(L.dimensoesReduzidas(1000, 3000), { largura: 533, altura: 1600 })
  assert.deepEqual(L.dimensoesReduzidas(800, 600), { largura: 800, altura: 600 })
  assert.deepEqual(L.dimensoesReduzidas(0, 600), { largura: 0, altura: 0 })
  assert.equal(L.FOTO_LADO_MAX, 1600)
  assert.equal(L.FOTO_QUALIDADE, 0.8)
  assert.equal(L.nomeJpg('IMG_0001.HEIC'), 'IMG_0001.jpg')
  assert.equal(L.nomeJpg('bolo.de.cenoura.png'), 'bolo.de.cenoura.jpg')
  assert.equal(L.nomeJpg(''), 'foto.jpg')
  assert.equal(L.problemaFoto({ size: 100, type: 'image/gif' }), 'Use uma foto JPG, PNG ou WebP.')
  assert.match(L.problemaFoto({ size: 6 * 1024 * 1024, type: 'image/png' }), /5 MB/)
  assert.equal(L.problemaFoto({ size: 6 * 1024 * 1024, type: 'image/jpeg' }, true), null, 'foto comprimida aqui pode chegar grande')
  assert.equal(L.problemaPdf({ size: 10, type: 'application/pdf', name: 'm.pdf' }), null)
  assert.match(L.problemaPdf({ size: 10, type: 'image/png', name: 'm.png' }), /PDF/)
  assert.match(L.problemaPdf({ size: 26 * 1024 * 1024, type: 'application/pdf', name: 'm.pdf' }), /25 MB/)
  assert.equal(L.tituloDoArquivo('Manual_A17  Pro.PDF'), 'Manual A17 Pro')
  assert.equal(L.tituloDoArquivo('x'.repeat(100) + '.pdf').length, 80)
  assert.equal(L.formatarTamanho(512), '512 B')
  assert.equal(L.formatarTamanho(2048), '2 KB')

  // Formulários.
  assert.deepEqual(L.linhasParaLista(' 1 ovo \n\n2 xícaras de farinha\n  '), ['1 ovo', '2 xícaras de farinha'])
  assert.equal(L.listaParaLinhas(['a', 'b']), 'a\nb')
  assert.deepEqual(L.tagsDoTexto('Lanche, rápido,lanche , ,Sem Glúten'), ['lanche', 'rápido', 'sem glúten'])
  assert.ok(L.youtubeValido('https://www.youtube.com/watch?v=abc'))
  assert.ok(L.youtubeValido('https://youtu.be/abc'))
  assert.ok(!L.youtubeValido('http://youtu.be/abc'), 'só https')
  assert.ok(!L.youtubeValido('https://vimeo.com/1'))
  assert.ok(L.PACOTE_ANDROID.test('com.whatsapp'))
  assert.ok(!L.PACOTE_ANDROID.test('whatsapp'))
  assert.equal(L.linkPlayStore('com.whatsapp'), 'https://play.google.com/store/apps/details?id=com.whatsapp')
  assert.equal(L.inteiroOuNull('12', 0, 120), 12)
  assert.equal(L.inteiroOuNull('', 0, 120), null)
  assert.equal(L.inteiroOuNull('121', 0, 120), null)
  assert.equal(L.inteiroOuNull('1,5', 0, 120), null)
  assert.equal(L.documentoLimpo('529.982.247-25'), '52998224725')
  assert.equal(L.documentoLimpo('12.abc.345/01de-35'), '12ABC34501DE35', 'CNPJ alfanumérico em maiúsculas')
  assert.deepEqual(L.diferencas({ a: 1, b: null, c: 'x' }, { a: 1, b: '2026-10-01', c: 'x' }), { b: '2026-10-01' })
  assert.deepEqual(L.diferencas({ a: false }, { a: false, novo: false }), { novo: false }, 'campo ausente conta como null')
  assert.equal(L.garantiaTexto({ meses_hardware: 3, meses_software_extra: 9 }), '3 meses + 9 de software')
  assert.equal(L.garantiaTexto({ meses_hardware: 1, meses_software_extra: 0 }), '1 mês')
  assert.equal(L.dataCurta('2026-10-06'), '06/10/2026')
  assert.equal(L.rotulo(L.EVENTO_ACESSO_LABEL, 'login'), 'Entrou no app')
  assert.equal(L.rotulo(L.EVENTO_ACESSO_LABEL, 'evento_novo'), 'evento novo')

  // Relatório incompleto não quebra a tela.
  assert.equal(L.normalizarRelatorio(null), null)
  const r = L.normalizarRelatorio({ quando: '2026-10-06T12:00:00Z', ok: false, erro: 'Site fora do ar' })
  assert.deepEqual(r.conflitos, [])
  assert.deepEqual(r.skus_site_sem_davinci, [])
  assert.equal(r.criados, 0)
  assert.equal(r.erro, 'Site fora do ar')

  // Selo: sem `quando` é o relatório vazio de quem nunca rodou (não é falha).
  assert.equal(L.seloRelatorio(null), 'nunca_rodou')
  assert.equal(L.seloRelatorio(L.normalizarRelatorio({ quando: null, ok: false, erro: 'A cópia do catálogo ainda não rodou.' })), 'nunca_rodou')
  assert.equal(L.seloRelatorio(r), 'falhou')
  assert.equal(L.seloRelatorio({ quando: '2026-10-06T12:00:00Z', ok: true }), 'ok')

  // Avisos, produtos inválidos e a conferência no DaVinci que não rodou
  // (`skus_site_sem_davinci: null`, servicos/catalogo_copia.py) chegam à tela.
  assert.equal(r.conferiu_davinci, true, 'campo ausente não é "não rodou"')
  assert.deepEqual(r.avisos, [])
  assert.deepEqual(r.produtos_invalidos, [])
  const semConferencia = L.normalizarRelatorio({
    quando: '2026-10-06T15:00:00Z', ok: true, erro: null, conflitos: [], linhas_desconhecidas: [],
    skus_site_sem_davinci: null, produtos_invalidos: [12, '#3'],
    avisos: ['Produtos do site com formato inválido ficaram de fora desta cópia.', 'Conferência com o DaVinci indisponível agora; tente de novo mais tarde.'],
  })
  assert.equal(semConferencia.conferiu_davinci, false)
  assert.deepEqual(semConferencia.skus_site_sem_davinci, [])
  assert.deepEqual(semConferencia.produtos_invalidos, ['12', '#3'])
  assert.equal(semConferencia.avisos.length, 2)
  assert.equal(L.pendenciasRelatorio(semConferencia), 3, '2 inválidos + a conferência que não rodou (os 2 avisos são deles)')
  assert.equal(L.pendenciasRelatorio(L.normalizarRelatorio({ quando: 'x', ok: true, avisos: ['Aviso novo da API.'] })), 1, 'aviso a mais conta')
  assert.equal(L.pendenciasRelatorio(L.normalizarRelatorio({ quando: 'x', ok: true, skus_site_sem_davinci: null })), 1)
  assert.equal(L.pendenciasRelatorio(L.normalizarRelatorio({ quando: 'x', ok: true, conflitos: [{}], linhas_desconhecidas: ['A'], skus_site_sem_davinci: ['dg1'] })), 3)
  assert.equal(L.pendenciasRelatorio(L.normalizarRelatorio({ quando: 'x', ok: true })), 0)
  assert.equal(L.pendenciasRelatorio(null), 0)

  // "Quem pediu" com os campos reais do requerente: nada some, lista é lista.
  const campos = L.camposRequerente(REQUERENTE)
  assert.deepEqual(campos.map((c) => c.label), [
    'E-mail', 'Celular', 'Pedidos comprovados', 'Pediu em', 'Aparelho', 'Integridade do aparelho',
    'Outros pedidos para este documento', 'Instalação do app',
  ])
  const valor = (k) => campos.find((c) => c.chave === k)
  assert.deepEqual(valor('pedidos_provados').valor, ['260901ABC123', '260915XYZ789'])
  assert.equal(valor('em').valor, L.dataHora(REQUERENTE.em))
  assert.notEqual(valor('em').valor, REQUERENTE.em, 'data formatada, não o ISO cru')
  assert.equal(valor('plataforma').valor, 'Android')
  assert.deepEqual([valor('integridade').valor, valor('integridade').alerta], ['só a básica', true])
  assert.deepEqual([valor('outros_casos_abertos').valor, valor('outros_casos_abertos').alerta], ['1 aberto quando pediu', true])
  const zero = L.camposRequerente({ ...REQUERENTE, integridade: 'ok', outros_casos_abertos: 0, pedidos_provados: [] })
  assert.deepEqual(zero.find((c) => c.chave === 'outros_casos_abertos').valor, 'nenhum')
  assert.deepEqual(zero.find((c) => c.chave === 'pedidos_provados').valor, [], 'lista vazia continua na tela')
  assert.equal(zero.find((c) => c.chave === 'integridade').alerta, false)
  // Campo que a tela não conhece: nome cru, objeto em JSON, lista como lista.
  const novo = L.camposRequerente({ email: 'a@exemplo.test', campo_novo: { a: 1 }, lista_nova: ['x', 2], vazio: '', nulo: null })
  assert.deepEqual(novo.map((c) => [c.label, c.valor]), [['E-mail', 'a@exemplo.test'], ['campo novo', '{"a":1}'], ['lista nova', ['x', '2']]])
  assert.deepEqual(L.camposRequerente({ erro: 'nao_decifrado' }), [])
  assert.deepEqual(L.camposRequerente(null), [])
}

// ---------------------------------------------------------------- middleware/app-uranyx.ts
{
  let usuario = null
  const destinos = []
  const mw = loadLib('../middleware/app-uranyx.ts', {
    defineNuxtRouteMiddleware: (fn) => fn,
    useAuthStore: () => ({ user: usuario }),
    navigateTo: (to) => { destinos.push(to); return to },
  }).default
  usuario = { id: 'a', role: 'admin', app_uranyx: true }
  assert.equal(mw({ path: '/app-uranyx/catalogo' }), undefined, 'liberado entra')
  usuario = { id: 'b', role: 'admin' }
  assert.equal(mw({ path: '/app-uranyx/catalogo' }), '/403', 'admin fora da lista não entra')
  usuario = { id: 'c', role: 'admin', app_uranyx: false }
  assert.equal(mw({ path: '/app-uranyx/catalogo' }), '/403')
  usuario = null
  assert.equal(mw({ path: '/app-uranyx/catalogo' }), '/login')
}

// ---------------------------------------------------------------- menu (AppSidebar.vue)
const nav = loadLib('../lib/navGroups.ts')
function menuPara(user, rota = '/') {
  const filename = path.resolve(__dirname, '../components/AppSidebar.vue')
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  let src = descriptor.scriptSetup.content
  const icones = (src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/) || [])[1]
  const nomesIcones = icones.split(',').map((s) => s.trim()).filter(Boolean)
  src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
  const js = transpile(src, ts.ModuleKind.ESNext) + '\nreturn { visibleSections, isActive }'
  const params = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: () => {}, onScopeDispose: () => {},
    allowedTabs: nav.allowedTabs, TABS_CADASTROS: nav.TABS_CADASTROS, TABS_NF: nav.TABS_NF, TABS_SISTEMA: nav.TABS_SISTEMA,
    withDefaults: (p, d) => ({ ...d, ...p }),
    defineProps: () => ({ collapsed: false }), defineEmits: () => () => {},
    useAuthStore: () => ({ user, isAdmin: user.role === 'admin' }),
    useRoute: () => ({ path: rota }),
    useRuntimeConfig: () => ({ public: { enableMarketing: false } }),
    useApi: () => ({ api: async () => ({ count: 0 }) }),
  }
  for (const n of nomesIcones) params[n] = {}
  const nomes = Object.keys(params)
  return new Function(...nomes, js)(...nomes.map((n) => params[n]))
}
{
  const ADMIN = { id: 'a', role: 'admin', status: 'active', permissions: {} }
  const todas = (m) => m.visibleSections.value.flatMap((s) => s.items)
  const liberado = menuPara({ ...ADMIN, app_uranyx: true }, '/app-uranyx/receitas')
  const item = todas(liberado).find((i) => i.to === '/app-uranyx/catalogo')
  assert.ok(item, 'admin liberado vê o App Uranyx')
  assert.equal(item.label, 'App Uranyx')
  assert.equal(item.adminOnly, true)
  assert.equal(item.appUranyxOnly, true)
  assert.ok(liberado.isActive(item), 'o item fica aceso em qualquer aba do módulo')
  assert.ok(!todas(menuPara(ADMIN)).some((i) => i.to.startsWith('/app-uranyx')), 'admin fora da lista não vê')
  assert.ok(!todas(menuPara({ id: 'u', role: 'user', status: 'active', permissions: {}, app_uranyx: true })).some((i) => i.to.startsWith('/app-uranyx')), 'não-admin não vê nem com a chave')
}

// ---------------------------------------------------------------- composables/useAppUranyx.ts
{
  let resposta = null
  const pedidos = []
  const C = loadLib('../composables/useAppUranyx.ts', {
    useApi: () => ({
      api: async (url, opts) => {
        pedidos.push({ url, opts })
        if (resposta instanceof Error) throw resposta
        return resposta
      },
    }),
    useState: (_k, init) => Vue.ref(init()),
  })
  const u = C.useAppUranyx()
  ;(async () => {
    resposta = [{ id: 'p1' }]
    assert.deepEqual(await u.chamar('/catalogo'), [{ id: 'p1' }])
    assert.equal(pedidos.at(-1).url, '/api/app-uranyx/catalogo', 'mesmo caminho do /admin, com o prefixo do DaVinci')

    resposta = Object.assign(new Error('503'), { status: 503, data: { erro: 'app_uranyx_indisponivel', mensagem: 'A API do app não respondeu em 10 s.' } })
    await assert.rejects(u.chamar('catalogo'))
    assert.equal(u.indisponivel.value, 'A API do app não respondeu em 10 s.', 'a frase da API vai para a tela')

    resposta = { quando: null, ok: true }
    const rel = await u.ultimoRelatorio()
    assert.equal(u.indisponivel.value, null, 'a próxima chamada que dá certo limpa o aviso')
    assert.deepEqual(rel.conflitos, [])
    assert.equal(pedidos.at(-1).url, '/api/app-uranyx/catalogo/sincronizacao')

    resposta = Object.assign(new Error('404'), { status: 404, data: { erro: 'nao_encontrado', mensagem: 'Nunca rodou' } })
    assert.equal(await u.ultimoRelatorio(), null, 'cópia que nunca rodou: sem relatório')

    resposta = { id: 'f1', url: 'https://api/conteudo/arquivos/f1', mime: 'application/pdf', tamanho: 3, nome: 'm' }
    await u.subirArquivo(new File(['%PDF'], 'manual.pdf', { type: 'application/pdf' }), 'Manual')
    const up = pedidos.at(-1)
    assert.equal(up.url, '/api/app-uranyx/arquivos')
    assert.equal(up.opts.method, 'POST')
    assert.ok(up.opts.body instanceof FormData)
    assert.equal(up.opts.body.get('arquivo').name, 'manual.pdf', 'multipart `arquivo`')
    assert.equal(up.opts.body.get('nome'), 'Manual')

    // Alteração com a API do app sem resposta a tempo (ou fora): a tela NÃO
    // troca pelo aviso; o erro vai para a ação, que mostra a frase do repasse.
    for (const [msg, opts] of [
      [MSG_DEMOROU, { method: 'PATCH', body: { titulo: 'x' } }],
      [MSG_DEMOROU, { method: 'post' }],
      [MSG_FORA_DO_AR, { method: 'DELETE' }],
    ]) {
      resposta = Object.assign(new Error('503'), { status: 503, data: { detail: { code: 'app_uranyx_indisponivel', message: msg } } })
      const erro = await u.chamar('receitas/r1', opts).then(() => null, (e) => e)
      assert.ok(erro, 'a alteração falha para quem chamou')
      assert.equal(u.indisponivel.value, null, `${opts.method}: a tela não troca`)
      assert.equal(L.erroAppUranyx(erro, 'Não deu para salvar').texto, msg)
    }
    // Leitura: troca (e a frase vai para a tela).
    resposta = Object.assign(new Error('503'), { status: 503, data: { detail: { code: 'app_uranyx_indisponivel', message: MSG_DEMOROU } } })
    await assert.rejects(u.chamar('receitas'))
    assert.equal(u.indisponivel.value, MSG_DEMOROU)
    await assert.rejects(u.chamar('receitas', { method: 'GET' }))
    assert.equal(u.indisponivel.value, MSG_DEMOROU)
    resposta = []
    await u.chamar('receitas')
    assert.equal(u.indisponivel.value, null)
  })().then(testarCatalogo).then(testarGaveta).then(testarRelatorio).then(testarFoto).then(testarContas)
    .then(testarContasSsr).then(testarReclamacoes).then(testarReceita).then(testarIndisponivel)
    .then(() => console.log('ok: app-uranyx-catalogo-sfc')).catch((e) => {
    console.error(e)
    process.exit(1)
  })
}

// ---------------------------------------------------------------- pages/app-uranyx/catalogo.vue
function fakeToasts() {
  const log = []
  const f = (kind) => (title, lines) => log.push({ kind, title, lines })
  return { log, success: f('success'), error: f('error'), warning: f('warning'), info: f('info') }
}

function rodarScript(descriptor, nomesRetorno, extras) {
  const src = descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const js = transpile(src, ts.ModuleKind.ESNext) + `\nreturn { ${nomesRetorno.join(', ')} }`
  const params = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, reactive: Vue.reactive, nextTick: Vue.nextTick,
    onMounted: (fn) => fn(),
    definePageMeta: () => {},
    Button: {},
    ...L,
    ...extras,
  }
  const icones = (descriptor.scriptSetup.content.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/) || [])[1] || ''
  for (const n of icones.split(',').map((s) => s.trim()).filter(Boolean)) params[n] = {}
  const nomes = Object.keys(params)
  return new Function(...nomes, js)(...nomes.map((n) => params[n]))
}

const PRODUTOS = [
  {
    id: 'p1', nome: 'Uranyx A17 Pro Max', categoria: 'celular', foto_url: 'https://x/a17.png',
    meses_hardware: 3, meses_software_extra: 9, tem_voltagem: false, logo_uranyx_desde: null, ativo: true,
    skus: [{ codigo_base: 'dg053', cor: 'Laranja', foto_url: null, origem: 'site' }, { codigo_base: 'dg900', cor: null, foto_url: null, origem: 'manual' }],
    site_produto_id: 12, fora_do_site: false, sincronizado_em: '2026-10-06T12:00:00-03:00', foto_travada: false,
  },
  {
    id: 'p2', nome: 'Air Fryer Uranyx 4L', categoria: 'eletrodomestico', foto_url: null,
    meses_hardware: 3, meses_software_extra: 0, tem_voltagem: true, logo_uranyx_desde: null, ativo: false,
    skus: [], site_produto_id: 30, fora_do_site: true, sincronizado_em: null,
  },
]

async function testarCatalogo() {
  const tpl = descritores.catalogo.template.content
  assert.match(tpl, /Sincronizar com o site/)
  assert.match(tpl, /<AppUranyxRelatorioSync :relatorio="relatorio"/)
  assert.match(tpl, /<AppUranyxProdutoGaveta v-model:open="gavetaAberta" :produto="selecionado" @salvo="aoSalvar" \/>/)
  // Badge por origem do SKU e "fora do site".
  assert.match(tpl, /s\.origem === 'manual' \? 'pill-muted' : 'pill-info'/)
  assert.match(tpl, /fora do site/)

  const pedidos = []
  const respostas = {
    catalogo: () => PRODUTOS.map((p) => ({ ...p })),
    'catalogo/sincronizar': () => ({
      quando: '2026-10-06T15:00:00Z', ok: true, erro: null, produtos_site: 24, criados: 2, atualizados: 22,
      fora_do_site: 1, skus_ligados: 51,
      conflitos: [{ codigo_base: 'dg053', produto_site: 'A17 Pro Max', ligado_a: 'Uranyx A17' }],
      linhas_desconhecidas: ['Promoções'], skus_site_sem_davinci: ['dg999'],
    }),
  }
  const toasts = fakeToasts()
  const indisponivel = Vue.ref(null)
  const tela = rodarScript(
    descritores.catalogo,
    ['produtos', 'relatorio', 'erro', 'carregar', 'sincronizar', 'filtrados', 'busca', 'categoria', 'situacao', 'contagem', 'abrir', 'selecionado', 'gavetaAberta', 'aoSalvar'],
    {
      useToasts: () => toasts,
      useAppUranyx: () => ({
        indisponivel,
        chamar: async (caminho, opts = {}) => {
          pedidos.push({ caminho, opts })
          if (!respostas[caminho]) throw Object.assign(new Error('x'), { status: 404, data: { detail: 'Not Found' } })
          return respostas[caminho]()
        },
        ultimoRelatorio: async () => null,
      }),
    },
  )
  await tick()
  await tick()
  assert.equal(tela.produtos.value.length, 2, 'carregou o catálogo no onMounted')
  assert.equal(pedidos[0].caminho, 'catalogo')
  assert.deepEqual(tela.contagem.value, { total: 2, ativos: 1, fora: 1, semSku: 1 })

  // Filtros: busca sem acento, por código, categoria e situação.
  tela.busca.value = 'air fryer'
  assert.deepEqual(tela.filtrados.value.map((p) => p.id), ['p2'])
  tela.busca.value = 'dg900'
  assert.deepEqual(tela.filtrados.value.map((p) => p.id), ['p1'], 'acha pelo código-base')
  tela.busca.value = ''
  tela.situacao.value = 'fora_do_site'
  assert.deepEqual(tela.filtrados.value.map((p) => p.id), ['p2'])
  tela.situacao.value = ''
  tela.categoria.value = 'celular'
  assert.deepEqual(tela.filtrados.value.map((p) => p.id), ['p1'])
  tela.categoria.value = ''

  // Sincronizar: POST, relatório na tela, toast com o resumo, lista relida.
  await tela.sincronizar()
  const sync = pedidos.find((p) => p.caminho === 'catalogo/sincronizar')
  assert.equal(sync.opts.method, 'POST')
  assert.equal(tela.relatorio.value.criados, 2)
  assert.equal(tela.relatorio.value.conflitos.length, 1)
  assert.equal(toasts.log.at(-1).kind, 'success')
  assert.match(toasts.log.at(-1).lines.join(' '), /24 produtos no site: 2 novos, 22 atualizados, 1 fora do site/)
  assert.match(toasts.log.at(-1).lines.join(' '), /3 itens para conferir/)
  assert.equal(pedidos.filter((p) => p.caminho === 'catalogo').length, 2, 'relê o catálogo depois da cópia')

  // Cópia certa, mas com produto inválido e sem a conferência no DaVinci: não é "Nada para conferir".
  respostas['catalogo/sincronizar'] = () => ({
    quando: '2026-10-06T16:00:00Z', ok: true, erro: null, produtos_site: 25, criados: 0, atualizados: 24,
    fora_do_site: 1, skus_ligados: 51, conflitos: [], linhas_desconhecidas: [], skus_site_sem_davinci: null,
    produtos_invalidos: ['77'],
    avisos: ['Produtos do site com formato inválido ficaram de fora desta cópia.', 'Conferência com o DaVinci indisponível agora; tente de novo mais tarde.'],
  })
  await tela.sincronizar()
  assert.equal(tela.relatorio.value.conferiu_davinci, false)
  assert.equal(toasts.log.at(-1).kind, 'success')
  assert.match(toasts.log.at(-1).lines.join(' '), /2 itens para conferir/)
  assert.ok(!/Nada para conferir/.test(toasts.log.at(-1).lines.join(' ')))

  // Cópia que falha: toast de erro com a frase do relatório.
  respostas['catalogo/sincronizar'] = () => ({ quando: null, ok: false, erro: 'O site respondeu 401: confira o SITE_CATALOGO_TOKEN.' })
  await tela.sincronizar()
  assert.equal(toasts.log.at(-1).kind, 'error')
  assert.match(toasts.log.at(-1).lines, /SITE_CATALOGO_TOKEN/)

  // Gaveta: abre no produto e o salvo volta para a lista.
  tela.abrir(tela.produtos.value[0])
  assert.equal(tela.gavetaAberta.value, true)
  assert.equal(tela.selecionado.value.id, 'p1')
  tela.aoSalvar({ ...tela.produtos.value[0], meses_hardware: 6 })
  assert.equal(tela.selecionado.value.meses_hardware, 6)

  // API do DaVinci sem o módulo / API do app fora: a lista mostra a frase.
  respostas.catalogo = () => { throw Object.assign(new Error('x'), { status: 503, data: { erro: 'app_uranyx_indisponivel' } }) }
  await tela.carregar()
  assert.equal(tela.erro.value, 'API do app fora do ar ou não configurada')
}

// ---------------------------------------------------------------- components/AppUranyxProdutoGaveta.vue
async function testarGaveta() {
  const d = descritores['AppUranyxProdutoGaveta.vue']
  const props = Vue.reactive({ open: true, produto: { ...PRODUTOS[0] } })
  const emitidos = []
  const pedidos = []
  const toasts = fakeToasts()
  let resposta = (caminho, opts) => ({ ...props.produto, ...(opts.body || {}) })
  const g = rodarScript(d, ['form', 'salvar', 'aoEnviarFoto', 'erros', 'ligar', 'novoCodigo', 'novaCor'], {
    defineProps: () => props,
    defineEmits: () => (ev, v) => emitidos.push([ev, v]),
    useToasts: () => toasts,
    useAppUranyx: () => ({
      chamar: async (caminho, opts = {}) => {
        pedidos.push({ caminho, opts })
        return resposta(caminho, opts)
      },
    }),
  })
  assert.equal(g.form.meses_hardware, 3, 'o formulário nasce com o produto')

  // Nada mudou: fecha sem PATCH.
  await g.salvar()
  assert.equal(pedidos.length, 0)
  assert.deepEqual(emitidos.at(-1), ['update:open', false])

  // Só o que mudou vai no PATCH; a foto nova vai como foto_arquivo_id.
  g.form.meses_software_extra = '12'
  g.form.foto_travada = true
  g.aoEnviarFoto({ id: 'arq-1', url: 'https://api/conteudo/arquivos/arq-1', mime: 'image/png', tamanho: 10, nome: 'f' })
  await g.salvar()
  assert.equal(pedidos.at(-1).caminho, 'catalogo/p1')
  assert.equal(pedidos.at(-1).opts.method, 'PATCH')
  assert.deepEqual(pedidos.at(-1).opts.body, { meses_software_extra: 12, foto_travada: true, foto_arquivo_id: 'arq-1' })
  assert.ok(emitidos.some(([ev]) => ev === 'salvo'))

  // Prazo fora da faixa não sai daqui.
  const antes = pedidos.length
  g.form.meses_hardware = '200'
  await g.salvar()
  assert.equal(pedidos.length, antes)
  assert.match(g.erros.value.join(' '), /0 a 120 meses/)
  g.form.meses_hardware = 3

  // Erro da API aparece com os campos.
  resposta = () => { throw Object.assign(new Error('x'), { status: 422, data: { erro: 'validacao', mensagem: 'Confira os dados e tente de novo.', campos: { logo_uranyx_desde: 'Data inválida' } } }) }
  g.form.logo_uranyx_desde = '2026-13-40'
  await g.salvar()
  assert.deepEqual(g.erros.value, ['Confira os dados e tente de novo.', 'Logo Uranyx desde: Data inválida'])

  // Ligar código: PUT no caminho codificado; código com sufixo é recusado aqui.
  resposta = () => ({ ...props.produto })
  g.novoCodigo.value = 'dg053.ci'
  const n = pedidos.length
  await g.ligar()
  assert.equal(pedidos.length, n, 'código com ponto não sai')
  g.novoCodigo.value = 'Kit Promo 01'
  g.novaCor.value = 'Preto'
  await g.ligar()
  assert.equal(pedidos.at(-1).caminho, 'catalogo/p1/skus/kit%20promo%2001')
  assert.equal(pedidos.at(-1).opts.method, 'PUT')
  assert.deepEqual(pedidos.at(-1).opts.body, { cor: 'Preto' })
}

// ---------------------------------------------------------------- components/AppUranyxRelatorioSync.vue
// Renderiza de verdade (SSR) o componente com o relatório.
const iconeFalso = { render: () => null }
ALIAS['lucide-vue-next'] = new Proxy({}, { get: () => iconeFalso })
// Componente falso que só mostra o título (se houver) e os slots.
const caixaFalsa = (tag = 'div') => ({
  props: ['title'],
  setup(p, { slots }) {
    return () => Vue.h(tag, [p.title || null, slots.default?.(), slots.rodape?.()])
  },
})
ALIAS['~/components/ui/button'] = { Button: caixaFalsa('button') }

// SSR de verdade (renderToString): `onMounted` não roda no servidor, como no Nuxt.
// `globais` são os auto-imports do Nuxt; `componentes`, os componentes globais.
async function renderizar(descriptor, props, { globais = {}, componentes = {} } = {}) {
  const { content } = compileScript(descriptor, { id: 'teste', inlineTemplate: true })
  const mod = {}
  const g = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, reactive: Vue.reactive, nextTick: Vue.nextTick,
    onMounted: Vue.onMounted, ...globais,
  }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  for (const [n, c] of Object.entries(componentes)) app.component(n, c)
  return renderToString(app)
}

async function testarRelatorio() {
  const d = descritores['AppUranyxRelatorioSync.vue']
  const nunca = await renderizar(d, { relatorio: L.normalizarRelatorio({ quando: null, ok: false, erro: 'A cópia do catálogo ainda não rodou.', ligada: true }) })
  assert.match(nunca, /ainda não rodou/)
  assert.match(nunca, /A cópia do catálogo ainda não rodou\./)
  assert.ok(!/falhou|pill-danger|Produtos no site|Nada para conferir/.test(nunca), 'cópia que nunca rodou não é falha nem tem números')

  const desligada = await renderizar(d, { relatorio: L.normalizarRelatorio({ quando: null, ok: false, erro: 'Cópia do catálogo desligada: configure SITE_CATALOGO_URL e SITE_CATALOGO_TOKEN na API do app.', ligada: false }) })
  assert.match(desligada, /ainda não rodou/)
  assert.match(desligada, /SITE_CATALOGO_URL/)
  assert.ok(!/falhou/.test(desligada))

  const semRelatorio = await renderizar(d, { relatorio: null })
  assert.match(semRelatorio, /ainda não rodou/)
  assert.ok(!/falhou/.test(semRelatorio))
  assert.match(await renderizar(d, { relatorio: null, carregando: true }), /carregando…/)

  const falhou = await renderizar(d, { relatorio: L.normalizarRelatorio({ quando: '2026-10-06T15:00:00Z', ok: false, erro: 'O site respondeu 401.' }) })
  assert.match(falhou, /pill-danger/)
  assert.match(falhou, /falhou/)
  assert.match(falhou, /O site respondeu 401\./)
  for (const so of [false, true]) {
    const html = await renderizar(d, { relatorio: L.normalizarRelatorio({ quando: '2026-10-06T15:00:00Z', ok: false, erro: 'O site respondeu 401.' }), soPendencias: so })
    assert.ok(!/Nada para conferir/.test(html), `cópia que falhou não é "Nada para conferir" (soPendencias=${so})`)
  }

  const certo = await renderizar(d, { relatorio: L.normalizarRelatorio({ quando: '2026-10-06T15:00:00Z', ok: true, produtos_site: 24 }) })
  assert.match(certo, /deu certo/)
  assert.match(certo, /Produtos no site/)
  assert.ok(!/ainda não rodou|falhou/.test(certo))
  assert.match(certo, /Nada para conferir/)
  assert.ok(!/não conferido/.test(certo))

  // Avisos, inválidos e a conferência que não rodou: aparecem, e nada de "Nada para conferir".
  const comAvisos = L.normalizarRelatorio({
    quando: '2026-10-06T16:00:00Z', ok: true, produtos_site: 25, conflitos: [], linhas_desconhecidas: [],
    skus_site_sem_davinci: null, produtos_invalidos: ['77', '#4'],
    avisos: ['Produtos do site com formato inválido ficaram de fora desta cópia.', 'Conferência com o DaVinci indisponível agora; tente de novo mais tarde.'],
  })
  for (const so of [false, true]) {
    const html = await renderizar(d, { relatorio: comAvisos, soPendencias: so })
    assert.ok(!/Nada para conferir/.test(html), `com avisos não é "Nada para conferir" (soPendencias=${so})`)
    assert.match(html, /Conferência com o DaVinci indisponível agora/)
    assert.match(html, /Produtos do site com formato inválido \(2\)/)
    assert.match(html, />77<\/code>/)
    assert.match(html, />#4<\/code>/)
    assert.match(html, /SKUs do site × DaVinci: não conferido/)
    assert.match(html, /deu certo/, 'a cópia em si deu certo')
  }
}

// ---------------------------------------------------------------- components/AppUranyxFoto.vue
async function testarFoto() {
  const d = descritores['AppUranyxFoto.vue']
  const toasts = fakeToasts()
  let envio = async () => { throw Object.assign(new Error('[POST] "/api/app-uranyx/arquivos": <no response> Failed to fetch'), { name: 'FetchError' }) }
  const props = { url: null, comprimir: false, rotulo: 'Foto', quadrada: false, desabilitado: false }
  const f = rodarScript(d, ['aoEscolher', 'erro', 'enviado'], {
    withDefaults: (p, padrao) => ({ ...padrao, ...p }),
    defineProps: () => props,
    defineEmits: () => () => {},
    useToasts: () => toasts,
    useAppUranyx: () => ({ subirArquivo: (...a) => envio(...a) }),
  })
  const escolher = (arquivo) => f.aoEscolher({ target: { files: [arquivo], value: 'c:/foto.png' } })
  const foto = new File(['x'], 'foto.png', { type: 'image/png' })

  // Sem rede no envio: frase em português, nunca o "Failed to fetch".
  await escolher(foto)
  assert.match(f.erro.value, /^Não deu para subir a foto: sem conexão com o servidor/)
  assert.ok(!/Failed to fetch|FetchError|no response/.test(f.erro.value))
  assert.equal(toasts.log.at(-1).lines, f.erro.value)

  // Erro de negócio da API continua com a frase dela.
  envio = async () => { throw Object.assign(new Error('x'), { status: 422, data: { erro: 'arquivo_invalido', mensagem: 'A foto está corrompida.' } }) }
  await escolher(foto)
  assert.equal(f.erro.value, 'A foto está corrompida.')

  // Erro de reduzir a foto (navegador) já vem em português e passa direto.
  props.comprimir = true
  const f2 = rodarScript(d, ['aoEscolher', 'erro'], {
    withDefaults: (p, padrao) => ({ ...padrao, ...p }),
    defineProps: () => props,
    defineEmits: () => () => {},
    useToasts: () => fakeToasts(),
    useAppUranyx: () => ({ subirArquivo: async () => { throw new Error('não devia subir') } }),
    comprimirFoto: async () => { throw new Error('Não deu para abrir esta foto.') },
  })
  await f2.aoEscolher({ target: { files: [foto], value: '' } })
  assert.equal(f2.erro.value, 'Não deu para abrir esta foto.')
}

// ---------------------------------------------------------------- pages/app-uranyx/contas.vue
async function testarContas() {
  const d = descritores.contas
  assert.match(d.template.content, /<AppUranyxConta v-if="conta" :conta="conta" @mudou="aoMudar" @fechar="fechar" \/>/)
  assert.match(descritores['AppUranyxConta.vue'].template.content, /aria-label="Fechar a conta"[^>]*@click="emit\('fechar'\)"/)

  const CONTA = (id) => ({ id, nome: `Pessoa ${id}`, documento_mascarado: '***.982.247-**', email: null, celular: null, estado: 'ativa', criado_em: null })
  const route = Vue.reactive({ query: { conta: 'c1', aba: 'x' } })
  const router = { replace: async ({ query }) => { route.query = query } }
  const pedidos = []
  let lento = null
  const respostas = {
    'contas/c1': () => CONTA('c1'),
    'contas/c2': () => CONTA('c2'),
    'contas/sumiu': () => { throw Object.assign(new Error('404'), { status: 404, data: { erro: 'nao_encontrado', mensagem: 'Conta não encontrada.' } }) },
    'contas/c3': () => { throw Object.assign(new Error('[GET] "/api/app-uranyx/contas/c3": <no response> Failed to fetch'), { name: 'FetchError' }) },
    'contas/lenta': () => new Promise((ok) => { lento = () => ok(CONTA('lenta')) }),
    'contas/busca': () => [CONTA('c1'), CONTA('c2')],
  }
  const t = rodarScript(d, ['conta', 'erroBusca', 'abrindo', 'fechar', 'verConta', 'documento', 'buscar'], {
    useRoute: () => route,
    useRouter: () => router,
    useAppUranyx: () => ({
      indisponivel: Vue.ref(null),
      chamar: async (caminho, opts = {}) => {
        pedidos.push({ caminho, opts })
        return respostas[caminho]()
      },
    }),
  })
  await tick()
  assert.equal(t.conta.value?.id, 'c1', '?conta= abre a conta')

  // Fechar tira o parâmetro (e só ele).
  t.fechar()
  await tick()
  assert.equal(t.conta.value, null)
  assert.deepEqual({ ...route.query }, { aba: 'x' })

  // Conta que não existe mais: aviso, nada da conta anterior na tela, sai da URL.
  t.verConta('c2')
  await tick()
  assert.equal(t.conta.value?.id, 'c2')
  t.verConta('sumiu')
  await tick()
  await tick()
  assert.equal(t.conta.value, null, 'não fica a conta anterior com a URL de outra')
  assert.equal(t.erroBusca.value, 'A conta deste link não existe mais no app.')
  assert.ok(!('conta' in route.query), 'o id que não existe sai da URL')
  assert.equal(t.abrindo.value, false)

  // Sem rede: frase amigável, a URL fica (para tentar de novo) e o mesmo "ver conta" reabre.
  t.verConta('c3')
  await tick()
  await tick()
  assert.match(t.erroBusca.value, /^Não deu para abrir a conta: sem conexão/)
  assert.equal(route.query.conta, 'c3')
  respostas['contas/c3'] = () => CONTA('c3')
  t.verConta('c3')
  await tick()
  assert.equal(t.conta.value?.id, 'c3', 'mesmo id na URL: abre direto')
  assert.equal(t.erroBusca.value, null)

  // Fechar enquanto abre: a resposta atrasada não traz a conta de volta.
  t.verConta('lenta')
  await tick()
  t.fechar()
  await tick()
  lento()
  await tick()
  assert.equal(t.conta.value, null)
  assert.equal(t.abrindo.value, false)
  assert.ok(!('conta' in route.query))

  // Busca com mais de uma conta: nenhuma aberta e nada velho na URL.
  t.verConta('c1')
  await tick()
  t.documento.value = '529.982.247-25'
  await t.buscar()
  await tick()
  assert.equal(t.conta.value, null)
  assert.ok(!('conta' in route.query))
}

// ---------------------------------------------------------------- SSR: pages/app-uranyx/contas.vue
// No servidor do Nuxt não sai GET nenhum (seria jogado fora e repetido no
// navegador); o `?conta=` do link abre no navegador (onMounted).
async function testarContasSsr() {
  const d = descritores.contas
  const route = Vue.reactive({ query: { conta: 'c1' } })
  const router = { replace: async ({ query }) => { route.query = query } }
  const pedidos = []
  ALIAS['~/composables/useAppUranyx'] = {
    useAppUranyx: () => ({
      indisponivel: Vue.ref(null),
      chamar: async (caminho, opts = {}) => {
        pedidos.push({ caminho, opts })
        return { id: 'c1', nome: 'Pessoa c1', documento_mascarado: '***.982.247-**', email: null, celular: null, estado: 'ativa', criado_em: null }
      },
    }),
  }
  const componentes = {}
  for (const n of ['RouteTabs', 'PageHeader', 'AppUranyxIndisponivel', 'AppUranyxConta', 'AppUranyxReclamacoes']) componentes[n] = caixaFalsa()
  await renderizar(d, {}, {
    globais: { definePageMeta: () => {}, useRoute: () => route, useRouter: () => router },
    componentes,
  })
  await tick()
  assert.deepEqual(pedidos, [], 'SSR de Contas: nenhum GET no servidor')

  // No navegador: o onMounted abre a conta do link (e só ela).
  const montar = []
  const t = rodarScript(d, ['conta'], {
    onMounted: (fn) => montar.push(fn),
    useRoute: () => route,
    useRouter: () => router,
    useAppUranyx: ALIAS['~/composables/useAppUranyx'].useAppUranyx,
  })
  await tick()
  assert.equal(pedidos.length, 0, 'nada antes de montar')
  montar.forEach((fn) => fn())
  await tick()
  assert.deepEqual(pedidos.map((p) => p.caminho), ['contas/c1'], '?conta= do link abre no navegador')
  assert.equal(t.conta.value?.id, 'c1')
}

// ---------------------------------------------------------------- components/AppUranyxReclamacoes.vue
const RECLAMACAO = {
  id: 'r1', estado: 'aberta', prazo: '2026-10-09T15:00:00Z', prazo_vencido: false, criado_em: '2026-10-06T15:00:00Z',
  decidido_em: null, decisao_nota: null,
  conta_atual: {
    id: 'c1', nome: 'Pessoa Teste', documento_mascarado: '***.982.247-**', email: 'atual@exemplo.test',
    celular: '11900000000', estado: 'congelada', criado_em: '2026-09-01T12:00:00Z',
  },
  requerente: { ...REQUERENTE },
}

async function testarReclamacoes() {
  const d = descritores['AppUranyxReclamacoes.vue']
  const pedidos = []
  ALIAS['~/composables/useAppUranyx'] = {
    useAppUranyx: () => ({
      chamar: async (caminho, opts = {}) => {
        pedidos.push({ caminho, opts })
        return [{ ...RECLAMACAO, requerente: { ...REQUERENTE } }]
      },
    }),
  }
  const globais = { useToasts: fakeToasts }
  const componentes = { Button: caixaFalsa('button') }

  // SSR: o onMounted não roda no servidor; nenhum GET sai de lá.
  await renderizar(d, {}, { globais, componentes })
  await tick()
  assert.deepEqual(pedidos, [], 'SSR do "Não fui eu": nenhum GET no servidor')

  // Depois de montar (aqui o onMounted vira serverPrefetch para o SSR esperar a carga).
  const html = await renderizar(d, {}, { globais: { ...globais, onMounted: (fn) => Vue.onServerPrefetch(fn) }, componentes })
  assert.deepEqual(pedidos.map((p) => [p.caminho, p.opts.query]), [['reclamacoes', { estado: 'aberta' }]])
  assert.match(html, /Pedidos comprovados:/)
  assert.match(html, />260901ABC123<\/code>/)
  assert.match(html, />260915XYZ789<\/code>/)
  assert.match(html, /Pediu em:/)
  assert.ok(html.includes(L.dataHora(REQUERENTE.em)), 'a data do pedido formatada')
  assert.ok(!html.includes(REQUERENTE.em), 'nada do ISO cru')
  assert.match(html, /E-mail:<\/dt><dd[^>]*>quem\.pediu@exemplo\.test/)
  assert.match(html, /Integridade do aparelho:<\/dt><dd[^>]*text-amber-700[^>]*>só a básica/)
  assert.match(html, /1 aberto quando pediu/)
  assert.match(html, /Aparelho:<\/dt><dd[^>]*>Android/)
  assert.ok(!/pedidos provados|\[object Object\]|instalacao id/.test(html), 'rótulos reais, sem nome cru')
  assert.match(html, /Aceitar \(exclui a conta atual\)/)

  // Requerente ilegível ou vazio: frase, nunca uma caixa vazia.
  pedidos.length = 0
  ALIAS['~/composables/useAppUranyx'].useAppUranyx = () => ({
    chamar: async () => [{ ...RECLAMACAO, id: 'r2', requerente: { erro: 'nao_decifrado' } }, { ...RECLAMACAO, id: 'r3', requerente: {} }],
  })
  const ilegivel = await renderizar(d, {}, { globais: { ...globais, onMounted: (fn) => Vue.onServerPrefetch(fn) }, componentes })
  assert.match(ilegivel, /Não deu para ler os dados de quem pediu\./)
  assert.match(ilegivel, /Sem dados de quem pediu\./)

  // No navegador: carrega ao montar e de novo quando muda a situação (sem o immediate).
  const chamadas = []
  const montar = []
  const t = rodarScript(d, ['estado', 'lista', 'requerentes'], {
    onMounted: (fn) => montar.push(fn),
    defineEmits: () => () => {},
    defineExpose: () => {},
    useToasts: fakeToasts,
    useAppUranyx: () => ({ chamar: async (caminho, opts = {}) => { chamadas.push(opts.query?.estado); return [{ ...RECLAMACAO }] } }),
  })
  await tick()
  assert.deepEqual(chamadas, [], 'nada antes de montar')
  montar.forEach((fn) => fn())
  await tick()
  assert.deepEqual(chamadas, ['aberta'])
  assert.deepEqual(t.requerentes.value.r1.find((c) => c.chave === 'pedidos_provados').valor, REQUERENTE.pedidos_provados)
  t.estado.value = 'todas'
  await tick()
  await tick()
  assert.deepEqual(chamadas, ['aberta', 'todas'])
}

// ---------------------------------------------------------------- components/AppUranyxReceitaGaveta.vue
async function testarReceita() {
  const d = descritores['AppUranyxReceitaGaveta.vue']
  const RECEITA = {
    id: 'rc1', titulo: 'Batata frita', foto_url: 'https://x/b.jpg', youtube_url: 'https://youtu.be/abc', tempo_min: 20,
    rendimento: '2 porções', dificuldade: 'facil', ingredientes: ['batata'], passos: ['fritar'], ajuste: null, tags: [],
    publicada: true,
    // x9 deixou de ser eletrodoméstico (não vem no GET /catalogo?categoria=eletrodomestico).
    eletrodomesticos: [{ id: 'e1', nome: 'Air Fryer 4L' }, { id: 'x9', nome: 'Liquidificador' }],
  }
  const ELETROS = [
    { ...PRODUTOS[1], id: 'e1', nome: 'Air Fryer 4L', ativo: true },
    { ...PRODUTOS[1], id: 'e2', nome: 'Panela elétrica', ativo: false },
  ]
  const props = Vue.reactive({ open: true, receita: RECEITA, eletros: ELETROS })
  const pedidos = []
  let resposta = (caminho, opts) => ({ ...RECEITA, ...(opts.body || {}) })
  const fakeUranyx = () => ({
    chamar: async (caminho, opts = {}) => {
      pedidos.push({ caminho, opts })
      return resposta(caminho, opts)
    },
  })
  const g = rodarScript(d, ['form', 'salvar', 'erros', 'opcoesEletros', 'fotoNova'], {
    defineProps: () => props,
    defineEmits: () => () => {},
    useToasts: fakeToasts,
    useAppUranyx: fakeUranyx,
  })
  const op = Object.fromEntries(g.opcoesEletros.value.map((o) => [o.id, o]))
  assert.deepEqual([op.x9.foraDaCategoria, op.e1.foraDaCategoria, op.e2.foraDaCategoria, op.e2.ativo], [true, false, false, false])
  assert.deepEqual([...g.form.eletrodomesticos], ['e1', 'x9'])

  // Seleção igual: o PATCH não leva a lista (nada de 422 por causa do x9).
  g.form.titulo = 'Batata frita crocante'
  await g.salvar()
  assert.deepEqual(g.erros.value, [])
  assert.equal(pedidos.at(-1).caminho, 'receitas/rc1')
  assert.equal(pedidos.at(-1).opts.method, 'PATCH')
  assert.equal(pedidos.at(-1).opts.body.titulo, 'Batata frita crocante')
  assert.ok(!('eletrodomesticos' in pedidos.at(-1).opts.body), 'seleção igual: sem eletrodomesticos')
  // Mesma seleção em outra ordem também não conta como mudança.
  g.form.eletrodomesticos = ['x9', 'e1']
  await g.salvar()
  assert.ok(!('eletrodomesticos' in pedidos.at(-1).opts.body))

  // Seleção mudou: vai a lista, já sem o que saiu da categoria.
  g.form.eletrodomesticos = ['e1', 'x9', 'e2']
  await g.salvar()
  assert.deepEqual(pedidos.at(-1).opts.body.eletrodomesticos, ['e1', 'e2'])
  g.form.eletrodomesticos = ['e1']
  await g.salvar()
  assert.deepEqual(pedidos.at(-1).opts.body.eletrodomesticos, ['e1'])

  // Só o que saiu da categoria (e mudou): não sai pedido, a frase explica.
  const n = pedidos.length
  g.form.eletrodomesticos = ['x9']
  await g.salvar()
  assert.equal(pedidos.length, n)
  assert.deepEqual(g.erros.value, ['Eletrodomésticos: marque pelo menos um que ainda seja eletrodoméstico.'])
  g.form.eletrodomesticos = []
  await g.salvar()
  assert.deepEqual(g.erros.value, ['Eletrodomésticos: marque pelo menos um.'])

  // Nova receita: a lista vai sempre.
  props.receita = null
  await tick()
  Object.assign(g.form, {
    titulo: 'Bolo', youtube_url: 'https://youtu.be/xyz', tempo_min: 40, rendimento: '8 fatias',
    ingredientes: 'farinha\novos', passos: 'misturar\nassar', eletrodomesticos: ['e1'],
  })
  g.fotoNova.value = { id: 'arq-9', url: 'https://api/conteudo/arquivos/arq-9', mime: 'image/jpeg', tamanho: 10, nome: 'b' }
  await g.salvar()
  assert.deepEqual(g.erros.value, [])
  assert.equal(pedidos.at(-1).opts.method, 'POST')
  assert.deepEqual(pedidos.at(-1).opts.body.eletrodomesticos, ['e1'])
  assert.equal(pedidos.at(-1).opts.body.foto_arquivo_id, 'arq-9')

  // Na tela: o rótulo certo para quem saiu da categoria; "(inativo)" só para o inativo de verdade.
  ALIAS['~/composables/useAppUranyx'] = { useAppUranyx: fakeUranyx }
  const html = await renderizar(d, { open: true, receita: RECEITA, eletros: ELETROS }, {
    globais: { useToasts: fakeToasts },
    componentes: { AppUranyxGaveta: caixaFalsa(), AppUranyxFoto: caixaFalsa() },
  })
  assert.match(html, /Liquidificador \(não é mais eletrodoméstico\)/)
  assert.match(html, /Panela elétrica \(inativo\)/)
  assert.ok(!/Liquidificador \(inativo\)/.test(html))
  assert.match(html, /Ao mudar a seleção, ele sai/)
  const semFora = await renderizar(d, { open: true, receita: { ...RECEITA, eletrodomesticos: [{ id: 'e1', nome: 'Air Fryer 4L' }] }, eletros: ELETROS }, {
    globais: { useToasts: fakeToasts },
    componentes: { AppUranyxGaveta: caixaFalsa(), AppUranyxFoto: caixaFalsa() },
  })
  assert.ok(!/não é mais eletrodoméstico|Ao mudar a seleção/.test(semFora))
}

// ---------------------------------------------------------------- components/AppUranyxIndisponivel.vue
async function testarIndisponivel() {
  const d = descritores['AppUranyxIndisponivel.vue']
  const componentes = { EmptyState: caixaFalsa() }
  const demorou = await renderizar(d, { mensagem: MSG_DEMOROU }, { componentes })
  assert.match(demorou, /API do app fora do ar ou não configurada/)
  assert.ok(demorou.includes(MSG_DEMOROU), 'a frase da API aparece')
  assert.ok(!/Nada foi alterado/i.test(demorou), 'tempo esgotado nunca diz "nada foi alterado"')
  assert.match(demorou, /demorou demais para responder/)

  const fora = await renderizar(d, { mensagem: MSG_FORA_DO_AR }, { componentes })
  assert.ok(fora.includes(MSG_FORA_DO_AR))
  assert.match(fora, /Nada foi alterado\./)
  assert.match(fora, /não conseguiu falar com a API do app/)
}
