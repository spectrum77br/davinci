// node tests/atendimento-leitura-parada.cjs — as lojas sem ler NO PRÓPRIO
// /atendimento (05/10/2026). A checagem antes de sair do Duoke achou a Temu
// sem ler desde 01/10 sem ninguém saber; o Eduardo decidiu: o aviso NÃO vai
// para a Ouvidoria — "já avisa ali no próprio atendimento". Aqui, a tela:
//  - o contrato com o backend (LeituraParadaOut no /resumo, campo a campo);
//  - os textos puros (o resumo "N lojas sem ler", a linha "loja (plataforma)
//    há quanto tempo: motivo", o title com o que fazer);
//  - a faixa renderizada (Vue SSR): vermelha, três na linha, "e mais N", o
//    link para a aba "Lojas e modo"; sem loja parada, nada;
//  - a página: a faixa no topo da Caixa (e na aba Lojas, sem o link), a marca
//    com a contagem na aba "Lojas e modo", e o resumo relido na aba Lojas;
//  - a Ouvidoria ficou como estava (sem o robô, sem as plataformas novas).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { descriptor, fonte, filename }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')

const plataforma = sfc('../components/AtendimentoPlataforma.vue')
const P = exportsDe(plataforma.descriptor)
const faixaSfc = sfc('../components/AtendimentoLeituraParada.vue')

// ------------------------------------------------ o contrato com o backend
{
  const schemas = api('schemas/atendimento.py')
  const bloco = (schemas.match(/^class LeituraParadaOut\(BaseModel\):([\s\S]*?)^class /m) || [])[1] || ''
  const campos = [...bloco.matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1])
  const tipo = (plataforma.descriptor.script.content.match(/export type LeituraParada = \{([\s\S]*?)\n\}/) || [])[1] || ''
  const chavesTs = [...tipo.matchAll(/^ {2}([a-z_]+)\??: /gm)].map((m) => m[1])
  assert.ok(campos.length >= 10, 'achou os campos do LeituraParadaOut')
  assert.deepEqual([...chavesTs].sort(), [...campos].sort(), 'o tipo da tela = o LeituraParadaOut')
  const resumoOut = (schemas.match(/^class ResumoOut\(BaseModel\):([\s\S]*?)^class /m) || [])[1] || ''
  assert.match(resumoOut, /^ {4}leitura_parada: list\[LeituraParadaOut\] = Field\(default_factory=list\)$/m)
  const rota = api('routers/atendimento.py')
  assert.match(rota, /leitura_parada = await _leitura_parada\(session, scope, canais\)/)
  assert.match(rota, /leitura_parada=leitura_parada,/)
  // O serviço: os tipos e o "interno" da geral/rodadas são os que a tela usa.
  const servico = api('services/vigia_leitura_atendimento.py')
  assert.match(servico, /^TIPO_LOJA = "loja"$/m)
  assert.match(servico, /^TIPO_GERAL = "geral"$/m)
  assert.match(servico, /^TIPO_RODADA = "rodada"$/m)
  assert.match(servico, /^PLATAFORMA_INTERNO = "interno"$/m)
  // Só leitura: nada de Ouvidoria nem Threema no serviço.
  assert.doesNotMatch(servico, /^from app\.services import ouvidoria|threema|OuvidoriaOcorrencia/m)
}

// ------------------------------------------------ os textos
const AGORA = Date.parse('2026-10-05T12:00:00Z')
const ha = (min) => new Date(AGORA - min * 60000).toISOString()
const lp = (o = {}) => ({
  chave: 'loja:1', tipo: 'loja', plataforma: 'temu', loja: 'Atv', motivo: 'sessão caiu no AdsPower',
  acao: 'Entrar de novo no Seller Center', desde: ha(4 * 1440), nunca_leu: false, minutos: 4 * 1440,
  limite_min: 60, integration_id: null, canal_id: 'c1', caixas: [], detalhe: 'perfil k1dkegpc no AdsPower', ...o,
})
{
  assert.deepEqual(P.leiturasParadas(null), [])
  assert.deepEqual(P.leiturasParadas({}), [], 'a API antiga não manda: sem faixa')
  assert.deepEqual(P.leiturasParadas({ leitura_parada: 'x' }), [])
  assert.equal(P.leiturasParadas({ leitura_parada: [lp()] }).length, 1)

  // Loja, plataforma, há quanto tempo e o motivo curto.
  assert.equal(P.linhaLeituraParada(lp(), AGORA), 'Atv (Temu) sem ler há 4 dias: sessão caiu no AdsPower')
  assert.equal(
    P.linhaLeituraParada(lp({ plataforma: 'ml', loja: 'Poofy', motivo: 'sem permissão', nunca_leu: true }), AGORA),
    'Poofy (Mercado Livre) nunca leu: sem permissão',
  )
  assert.equal(
    P.linhaLeituraParada(lp({ plataforma: 'shopee', loja: 'atv', motivo: 'erro na leitura', desde: ha(45), minutos: 45 }), AGORA),
    'atv (Shopee) sem ler há 45 min: erro na leitura',
  )
  // O relógio da tela anda: sem `desde`, os minutos que o backend contou.
  assert.equal(P.linhaLeituraParada(lp({ desde: null, minutos: 130 }), AGORA), 'Atv (Temu) sem ler há 2 h 10 min: sessão caiu no AdsPower')
  // A geral e as rodadas não têm plataforma.
  const geral = lp({ chave: 'geral:lojas', tipo: 'geral', plataforma: 'interno', loja: 'Leitura das lojas', motivo: 'nenhuma loja lê', desde: ha(40), minutos: 40 })
  assert.equal(P.linhaLeituraParada(geral, AGORA), 'Nenhuma loja lê há 40 min')
  const rodada = lp({ chave: 'rodada:avaliacoes', tipo: 'rodada', plataforma: 'interno', loja: 'Avaliações de venda', motivo: 'rodada não termina lendo', desde: ha(180), minutos: 180 })
  assert.equal(P.linhaLeituraParada(rodada, AGORA), 'Avaliações de venda sem ler há 3 h: rodada não termina lendo')
  assert.equal(P.linhaLeituraParada({ ...rodada, nunca_leu: true }, AGORA), 'Avaliações de venda nenhuma rodada leu: rodada não termina lendo')

  // O resumo da faixa (e o title da marca da aba).
  assert.equal(P.tituloLeituraParada([lp()]), '1 loja sem ler')
  assert.equal(P.tituloLeituraParada([lp(), lp({ chave: 'loja:2' }), lp({ chave: 'canal:3', plataforma: 'site' })]), '3 lojas sem ler')
  assert.equal(P.tituloLeituraParada([geral, lp(), rodada]), 'Nenhuma loja lê · 1 loja sem ler · 1 rodada parada')
  assert.equal(P.tituloLeituraParada([geral]), 'Nenhuma loja lê')
  assert.equal(P.tituloLeituraParada([]), '')

  // O title: as caixas (quando a loja tem mais de uma), o erro e o que fazer.
  assert.equal(
    P.dicaLeituraParada(lp({ caixas: ['Perguntas', 'Pós-venda'], detalhe: 'HTTP 403', acao: 'Reautorizar' })),
    'Caixas: Perguntas, Pós-venda — HTTP 403 — O que fazer: Reautorizar',
  )
  assert.equal(P.dicaLeituraParada(lp({ caixas: ['Chat'], detalhe: null, acao: 'X' })), 'O que fazer: X')
}

// ------------------------------------------------ a faixa renderizada (setup de verdade)
function montarFaixa(props) {
  const script = compileScript(faixaSfc.descriptor, { id: 'faixa', inlineTemplate: false })
  const req = (nome) => {
    if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
    if (nome === '~/components/AtendimentoPlataforma.vue') return P
    return require(nome)
  }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', 'ref', 'computed', transpile(script.content))(
    req, mod, mod.exports, Vue.ref, Vue.computed,
  )
  const emitidos = []
  const reativo = Vue.reactive({ link: true, ...props })
  const estado = Vue.proxyRefs(mod.exports.default.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }))
  return { estado, emitidos, reativo }
}
async function renderFaixa(m) {
  const tpl = compileTemplate({ source: faixaSfc.descriptor.template.content, filename: faixaSfc.filename, id: 'faixa' })
  const mod = {}
  new Function('exports', 'require', transpile(tpl.code))(mod, require)
  const app = Vue.createSSRApp({ setup: () => ({ ...Vue.toRefs(m.reativo), ...m.estado }), render: mod.render })
  app.component('CircleAlert', { render: () => Vue.h('i') })
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

async function principal() {
  // Sem loja parada: nada (a Caixa não perde altura).
  {
    const html = await renderFaixa(montarFaixa({ itens: [], agora: AGORA }))
    assert.equal(html.trim(), '')
  }

  // Cinco lojas: vermelha, o resumo, três na linha, "e mais 2" e o link.
  const itens = [
    lp({ chave: 'canal:t1' }),
    lp({ chave: 'loja:p', plataforma: 'ml', loja: 'Poofy', motivo: 'sem permissão', nunca_leu: true, caixas: ['Perguntas', 'Pós-venda'], detalhe: 'HTTP 403', acao: 'Reautorizar o app' }),
    lp({ chave: 'loja:l', plataforma: 'ml', loja: 'lucas mei', motivo: 'token não renova', nunca_leu: true }),
    lp({ chave: 'loja:i', plataforma: 'tiktok', loja: 'inova', motivo: 'sem permissão', nunca_leu: true }),
    lp({ chave: 'loja:x', plataforma: 'tiktok', loja: 'Poofy', motivo: 'sem permissão', nunca_leu: true }),
  ]
  const m = montarFaixa({ itens, agora: AGORA })
  let html = await renderFaixa(m)
  assert.match(html, /role="status"/)
  assert.match(html, /data-leitura-parada[^-]/)
  assert.match(html, /border-red-500\/40 bg-red-500\/10/, 'a faixa é vermelha (e discreta: uma linha)')
  assert.match(html, />5 lojas sem ler</)
  const naLinha = [...html.matchAll(/data-leitura-parada-item[^>]*>([^<]*)</g)].map((x) => x[1])
  assert.deepEqual(naLinha, [
    'Atv (Temu) sem ler há 4 dias: sessão caiu no AdsPower',
    'Poofy (Mercado Livre) nunca leu: sem permissão',
    'lucas mei (Mercado Livre) nunca leu: token não renova',
  ])
  assert.match(html, /title="Caixas: Perguntas, Pós-venda — HTTP 403 — O que fazer: Reautorizar o app"/)
  assert.match(html, /aria-expanded="false"[^>]*>e mais 2</)
  assert.match(html, /data-leitura-parada-link[^>]*>ver em Lojas e modo</)

  // Aberta: todas, cada uma com o que fazer.
  m.estado.aberta = true
  html = await renderFaixa(m)
  const todas = [...html.matchAll(/<li[^>]*data-leitura-parada-item[^>]*>([\s\S]*?)<\/li>/g)].map((x) => x[1].replace(/<[^>]+>/g, '').trim())
  assert.equal(todas.length, 5)
  assert.equal(todas[1], 'Poofy (Mercado Livre) nunca leu: sem permissão — Reautorizar o app')
  assert.match(html, /aria-expanded="true"[^>]*>esconder</)

  // Até três: o botão vira "o que fazer?". Na aba Lojas, sem o link.
  const poucas = montarFaixa({ itens: itens.slice(0, 2), agora: AGORA, link: false })
  html = await renderFaixa(poucas)
  assert.match(html, />2 lojas sem ler</)
  assert.match(html, />o que fazer\?</)
  assert.doesNotMatch(html, /ver em Lojas e modo/)

  // O link pede a aba "Lojas e modo" (a página troca a aba).
  assert.match(faixaSfc.descriptor.template.content, /@click="emit\('abrir-lojas'\)"/)
}

// ------------------------------------------------ a página
{
  const pagina = web('pages/atendimento.vue')
  assert.match(pagina, /const semLer = computed\(\(\) => leiturasParadas\(resumo\.value\)\)/)
  // A faixa no topo da Caixa, com o link; na aba Lojas, sem ele.
  const faixa = (pagina.match(/<AtendimentoLeituraParada[\s\S]*?\/>/) || [])[0] || ''
  assert.ok(faixa, 'a faixa está na página')
  assert.match(faixa, /v-if="aba === 'caixa' \|\| aba === 'lojas'"/)
  assert.match(faixa, /:itens="semLer"/)
  assert.match(faixa, /:link="aba === 'caixa'"/)
  assert.match(faixa, /@abrir-lojas="aba = 'lojas'"/)
  // Dentro do bloco medido acima da Caixa (a altura da Caixa conta com ela).
  const iTopo = pagina.indexOf('<div ref="topoEl"')
  const iFaixa = pagina.indexOf('<AtendimentoLeituraParada')
  const iCaixa = pagina.indexOf('<!-- Caixa: lojas | fila | conversa + pedido -->')
  assert.ok(iTopo > 0 && iTopo < iFaixa && iFaixa < iCaixa)
  // A marca com a contagem na aba "Lojas e modo".
  assert.match(pagina, /v-if="a\.value === 'lojas' && semLer\.length"[\s\S]{0,400}data-marca-leitura-parada\s*>\{\{ semLer\.length \}\}<\/span>/)
  assert.match(pagina, /:title="tituloLeituraParada\(semLer\)"/)
  // Some sozinha: o resumo é relido a cada 30 s na Caixa e também na aba Lojas.
  assert.match(pagina, /if \(aba\.value === 'lojas'\) return carregarResumo\(\)\n\s+if \(aba\.value !== 'caixa'\) return\n\s+await Promise\.all\(\[atualizarLista\(\), carregarResumo\(\)\]\)/)
}

// ------------------------------------------------ a Ouvidoria ficou como estava
{
  const plataformasOuv = web('components/OuvidoriaPlataforma.vue')
  for (const p of ['temu', 'aliexpress', 'instagram', 'facebook']) {
    assert.doesNotMatch(plataformasOuv, new RegExp(`^\\s+${p}: \\{`, 'm'), `${p} não entrou na Ouvidoria`)
  }
  assert.doesNotMatch(web('pages/ouvidoria/robos.vue'), /startsWith\('\/atendimento'\)/)
  assert.doesNotMatch(api('services/ouvidoria.py'), /vigia_leitura_atendimento|lista_geral/)
  assert.doesNotMatch(api('routers/ouvidoria.py'), /vigia_leitura_atendimento|_robos_ocultos/)
  assert.doesNotMatch(api('worker.py'), /vigia_leitura_atendimento_tick/)
}

principal().then(
  () => console.log('ok: atendimento-leitura-parada'),
  (e) => { console.error(e); process.exit(1) },
)
