// node tests/atendimento-direct.cjs — o Direct do Instagram SEPARADO POR CONTA (frente C, 02/10/2026).
// O que a tela tem de garantir:
//  - a barra de lojas: uma linha por conta (7buyers, Charlots, Uranyx), cada
//    uma com a SUA bolinha; a conta com a caixa de comentários sem permissão
//    (token sem o escopo novo) continua ACESA quando há Direct esperando — o
//    Direct é outra leitura e apagar esconderia a bolinha dele —, e apaga só
//    sem Direct esperando; o title separa Direct × comentários (a 7buyers só
//    tem Direct); a linha de conta fora do cadastro (sem `rede_social_id`)
//    APARECE (revisão 02/10: sem ela a soma das linhas não batia com o
//    número da plataforma) e, clicada, filtra o Instagram inteiro;
//  - a lista: em tela estreita o seletor de loja oferece as contas (e os
//    sites) e liga o filtro certo; com a caixa de comentários do Instagram
//    lida, a linha diz "@charlots_br · Direct" e aparece o seletor de caixa
//    (Direct × Comentários).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel) })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  new Function('exports', 'require', transpile(compiled.code))({}, require)
  return descriptor
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return src.slice(a, b)
}

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
// As funções puras do <script> da barra (o corte pela plataforma do topo,
// 09/10/2026) — o trecho do setup usa elas.
const LJ = (() => {
  const F = exportsDe(sfc('../components/AtendimentoFiltroPlataforma.vue'))
  const req = (n) => (n === '~/components/AtendimentoFiltroPlataforma.vue' ? F : n === '~/components/AtendimentoPlataforma.vue' ? P : require(n))
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(lojasSfc.script.content))(req, mod, mod.exports)
  return mod.exports
})()
const CORTE = ['corteDaBarra', 'gruposVisiveis', 'nomeDoCorte', 'ativaTodasDoCorte', 'filtrosTodasDoCorte', 'numeroDoCorte', 'filtrosDoGrupo']
const listaSfc = sfc('../components/AtendimentoLista.vue')

const vazio = { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '' }
const canalComentarios = { id: 'c-ig', integration_id: null, externo_ref: 'rede:instagram:1784', rede_social_id: 'rs-ch', plataforma: 'instagram', canal: 'comentario', status: 'sem_escopo' }
// O /resumo de produção com o token antigo: a caixa de comentários da
// Charlots sem permissão; o Direct das três contas lendo.
const resumo = {
  plataformas: [
    { plataforma: 'shopee', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'instagram', aguardando: 5, vencendo: 0, vencidas: 1 },
  ],
  lojas: [
    { integration_id: 'i1', plataforma: 'shopee', conta: 'ATV', aguardando: 1, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, externo_ref: 'site:charlots', plataforma: 'site', conta: 'Charlots', aguardando: 0, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, externo_ref: 'rede:instagram:1784', rede_social_id: 'rs-ch', plataforma: 'instagram', conta: '@charlots_br', aguardando: 2, vencidas: 0, direct_aguardando: 2, status_canal: 'sem_escopo', status_motivo: 'Sem permissão no token do DaVinci Publicador (falta o escopo de comentários: gere o token novo): (#10)' },
    { integration_id: null, rede_social_id: 'rs-ur', plataforma: 'instagram', conta: '@uranyx_br', aguardando: 1, vencidas: 1, direct_aguardando: 1 },
    { integration_id: null, rede_social_id: 'rs-7b', plataforma: 'instagram', conta: '@7buyers_br', aguardando: 1, vencidas: 0, direct_aguardando: 1 },
    // DMs de conta que saiu do cadastro: linha própria (filtra a plataforma).
    { integration_id: null, rede_social_id: null, plataforma: 'instagram', conta: 'Direct (conta fora do cadastro)', aguardando: 1, vencidas: 0, direct_aguardando: 1 },
  ],
  canais: [
    { id: 'c-shopee', integration_id: 'i1', plataforma: 'shopee', canal: 'chat', status: 'ok' },
    canalComentarios,
  ],
  flags: {},
}

// ------------------------------------------------ a barra de lojas
const setup = lojasSfc.scriptSetup.content
function barra(r, filtrosIniciais = {}) {
  const filtros = Vue.ref({ ...vazio, ...filtrosIniciais })
  const corpo = transpile(trecho(setup, 'const ORDEM', 'function escolherTodas') + '\n' + setup.slice(setup.indexOf('function escolherTodas')))
    + '\nreturn { lojas, grupos, total, mostrarInstagram, motivo, escolherLoja, ativaLoja }'
  const out = new Function('computed', 'props', 'filtros', 'viaRobo', 'statusCanalCodigo', 'semLeitura', 'leituraParada', 'nomeDoGrupo', 'plataformaInfo', 'sellerCenterDe', 'statusCanalInfo', ...CORTE, corpo)(
    Vue.computed, { resumo: r }, filtros, P.viaRobo, P.statusCanalCodigo, P.semLeitura, P.leituraParada, P.nomeDoGrupo, P.plataformaInfo, P.sellerCenterDe, P.statusCanalInfo, ...CORTE.map((n) => LJ[n]),
  )
  return { ...out, filtros }
}
{
  const b = barra(resumo)
  const ig = b.grupos.value.find((g) => g.plataforma === 'instagram')
  assert.ok(ig, 'o grupo Instagram')
  assert.deepEqual(ig.lojas.map((l) => l.conta).sort(), ['@7buyers_br', '@charlots_br', '@uranyx_br', 'Direct (conta fora do cadastro)'].sort(), 'uma linha por conta, mais a das DMs de conta fora do cadastro')
  assert.equal(b.mostrarInstagram.value, false, 'sem a linha única "Direct"')
  const por = Object.fromEntries(ig.lojas.map((l) => [l.conta, l]))
  // A bolinha de cada conta.
  assert.deepEqual([por['@7buyers_br'], por['@charlots_br'], por['@uranyx_br'], por['Direct (conta fora do cadastro)']].map((l) => l.nao_lidas), [1, 2, 1, 1])
  // A soma das linhas bate com o número da plataforma (o /resumo: 5).
  const plataformaIg = resumo.plataformas.find((p) => p.plataforma === 'instagram')
  assert.equal(ig.lojas.reduce((t, l) => t + l.nao_lidas, 0), plataformaIg.aguardando, 'soma das linhas = total do Instagram')
  // A linha da conta fora do cadastro: acesa, explica, e filtra o Instagram inteiro.
  const fora = por['Direct (conta fora do cadastro)']
  assert.deepEqual([fora.apagada, fora.rede_social_id, fora.integration_id], [false, '', ''])
  assert.match(b.motivo(fora), /Direct de conta que não está em Cadastros › Redes Sociais: 1 esperando/)
  assert.match(b.motivo(fora), /Clicar mostra as conversas de todas as lojas Instagram/)
  b.escolherLoja(fora)
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.rede_social_id, b.filtros.value.externo_ref, b.filtros.value.integration_id], ['instagram', '', '', ''])
  // Charlots: comentários sem permissão, mas Direct esperando → acesa, com alerta.
  assert.equal(por['@charlots_br'].apagada, false)
  assert.equal(por['@charlots_br'].parcial, true)
  assert.equal(por['@charlots_br'].comentarios, true)
  assert.equal(ig.nao_lidas, 5, 'o grupo conta a Charlots (o Direct dela) e a conta fora do cadastro')
  assert.equal(b.total.value, 6, 'Todas = Shopee 1 + Instagram 5')
  const titulo = b.motivo(por['@charlots_br'])
  assert.match(titulo, /Atenção — Sem permissão no token do DaVinci Publicador/)
  assert.match(titulo, /Direct: 2 esperando \(só leitura\) · comentários: 0 esperando/)
  // 7buyers: só Direct — o title não fala de comentários que não existem.
  assert.equal(por['@7buyers_br'].comentarios, false)
  assert.equal(por['@7buyers_br'].apagada, false)
  assert.match(b.motivo(por['@7buyers_br']), /Direct: 1 esperando \(só leitura\) — a conta não tem leitura de comentários/)
  assert.match(b.motivo(por['@uranyx_br']), /1 vencida\(s\)/)
  // Clicar na conta filtra por ela (o Direct e os comentários dela).
  b.escolherLoja(por['@uranyx_br'])
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.rede_social_id, b.filtros.value.externo_ref, b.filtros.value.integration_id], ['instagram', 'rs-ur', '', ''])
  assert.ok(b.ativaLoja(por['@uranyx_br']))
  assert.ok(!b.ativaLoja(por['@charlots_br']))
}
{
  // Sem Direct nenhum (nem esperando, nem `direct_total`), a caixa de
  // comentários sem permissão apaga a linha (e o cadeado diz o porquê).
  const r = { ...resumo, lojas: resumo.lojas.map((l) => (l.rede_social_id === 'rs-ch' ? { ...l, aguardando: 0, direct_aguardando: 0 } : l)) }
  const ch = barra(r).lojas.value.find((l) => l.rede_social_id === 'rs-ch')
  assert.equal(ch.apagada, true)
  assert.equal(ch.parcial, false)
}
{
  // Sem Direct esperando, mas a conta TEM Direct (`direct_total`): acesa com o
  // alerta — o Direct continua sendo lido (02/10/2026, integrador).
  const r = { ...resumo, lojas: resumo.lojas.map((l) => (l.rede_social_id === 'rs-ch' ? { ...l, aguardando: 0, direct_aguardando: 0, direct_total: 3 } : l)) }
  const ch = barra(r).lojas.value.find((l) => l.rede_social_id === 'rs-ch')
  assert.deepEqual([ch.apagada, ch.parcial, ch.direct_total], [false, true, 3])
}
{
  // Caixa de comentários com erro e Direct esperando: também acesa.
  const r = { ...resumo, lojas: resumo.lojas.map((l) => (l.rede_social_id === 'rs-ch' ? { ...l, status_canal: 'erro' } : l)) }
  const ch = barra(r).lojas.value.find((l) => l.rede_social_id === 'rs-ch')
  assert.deepEqual([ch.apagada, ch.parcial], [false, true])
}
{
  // A loja de marketplace sem leitura continua apagada (a regra é só da conta de rede).
  const r = { ...resumo, lojas: resumo.lojas.map((l) => (l.integration_id === 'i1' ? { ...l, status_canal: 'sem_escopo' } : l)), canais: [{ ...resumo.canais[0], status: 'sem_escopo' }] }
  const shopee = barra(r).lojas.value.find((l) => l.integration_id === 'i1')
  assert.equal(shopee.apagada, true)
}

// ------------------------------------------------ a lista
const L = listaSfc.scriptSetup.content
function lista(r, filtrosIniciais = {}) {
  const filtros = Vue.ref({ ...vazio, ...filtrosIniciais })
  const corpo = transpile(
    trecho(L, 'function mudar', '// Busca com espera')
    + '\n' + trecho(L, '// O seletor de loja', '// ─── itens')
    + '\n' + trecho(L, 'function titulo', '// Prévia como no Duoke'),
  ) + '\nreturn { lojas, lojaEscolhida, escolherLoja, canais, loja }'
  const out = new Function('computed', 'props', 'filtros', 'canaisDa', 'canalLabel', 'variasCaixas', 'plataformaInfo', corpo)(
    Vue.computed, { resumo: r }, filtros, P.canaisDa, P.canalLabel, P.variasCaixas, P.plataformaInfo,
  )
  return { ...out, filtros }
}
{
  const l = lista(resumo)
  // O seletor de loja (tela estreita): lojas com integração, sites e contas.
  assert.deepEqual(l.lojas.value.map((x) => x.chave), ['r:rs-7b', 'r:rs-ch', 'r:rs-ur', 'i:i1', 'e:site:charlots'])
  assert.equal(l.lojaEscolhida.value, '')
  l.escolherLoja('r:rs-ch')
  assert.deepEqual([l.filtros.value.plataforma, l.filtros.value.rede_social_id, l.filtros.value.externo_ref, l.filtros.value.integration_id, l.filtros.value.canal], ['instagram', 'rs-ch', '', '', ''])
  assert.equal(l.lojaEscolhida.value, 'r:rs-ch')
  // Com a plataforma escolhida, só as contas dela.
  assert.deepEqual(l.lojas.value.map((x) => x.chave), ['r:rs-7b', 'r:rs-ch', 'r:rs-ur'])
  // Caixas do Instagram (há caixa de comentários): Direct × Comentários.
  assert.deepEqual(l.canais.value.map((c) => c.value), ['dm', 'comentario'])
  // "todas lojas" tira a conta e fica na plataforma.
  l.escolherLoja('')
  assert.deepEqual([l.filtros.value.plataforma, l.filtros.value.rede_social_id, l.filtros.value.integration_id], ['instagram', '', ''])
  // O site filtra pela origem; a loja com integração, pela integração.
  l.filtros.value = { ...l.filtros.value, plataforma: '' }
  l.escolherLoja('e:site:charlots')
  assert.deepEqual([l.filtros.value.plataforma, l.filtros.value.externo_ref, l.filtros.value.rede_social_id], ['site', 'site:charlots', ''])
  assert.equal(l.lojaEscolhida.value, 'e:site:charlots')
  l.filtros.value = { ...l.filtros.value, plataforma: '' }
  l.escolherLoja('i:i1')
  assert.deepEqual([l.filtros.value.integration_id, l.filtros.value.externo_ref, l.filtros.value.rede_social_id], ['i1', '', ''])
  assert.equal(l.lojaEscolhida.value, 'i:i1')
  // A linha diz de qual caixa é a conversa da conta.
  assert.equal(l.loja({ plataforma: 'instagram', canal: 'dm', conta: '@charlots_br' }), '@charlots_br · Direct')
  assert.equal(l.loja({ plataforma: 'instagram', canal: 'comentario', conta: '@charlots_br' }), '@charlots_br · Comentário')
  assert.equal(l.loja({ plataforma: 'shopee', canal: 'chat', conta: 'ATV' }), 'ATV')
}
{
  // Sem caixa de comentários (só o Direct): sem seletor de caixa nem sufixo.
  const l = lista({ ...resumo, canais: [resumo.canais[0]] }, { plataforma: 'instagram' })
  assert.deepEqual(l.canais.value, [])
  assert.equal(l.loja({ plataforma: 'instagram', canal: 'dm', conta: '@7buyers_br' }), '@7buyers_br')
}
// O seletor de loja usa a chave (não o integration_id) e não fica travado no Instagram.
const tpl = listaSfc.template.content
assert.match(tpl, /:value="lojaEscolhida"/)
assert.match(tpl, /@change="escolherLoja\(\(\$event\.target as HTMLSelectElement\)\.value\)"/)
assert.doesNotMatch(tpl, /:disabled="filtros\.plataforma === 'instagram'"/)

// ------------------------------------------------ o contrato com a API
const router = fs.readFileSync(path.resolve(__dirname, '../../api/app/routers/atendimento.py'), 'utf8')
assert.match(router, /barra = _com_direct\(barra, contas_ig\)/, 'o /resumo manda uma linha por conta')
const adaptador = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/atendimento/instagram.py'), 'utf8')
assert.match(adaptador, /"conta": arroba\(conversa\.conta\)/, 'a conta do Direct com o @, como a barra')

console.log('ok — atendimento Direct por conta')
