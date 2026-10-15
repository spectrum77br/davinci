// node tests/atendimento-numeros-duoke.cjs — os NÚMEROS da Caixa iguais aos do Duoke (14/10/2026).
// O dono: "mesmo assim o nosso ATV mostra 9, o deles 5 … tá pegando uns
// antigos de avaliação". Medido no Duoke (14/10 13:47, aba Shopee): o número
// ao lado da loja é a SOMA DAS MENSAGENS NÃO LIDAS do chat esperando resposta
// (ATV 5 = uma conversa com 4 + outra com 1) — o `nao_lidas_duoke` do /resumo
// (services/atendimento/pendente.py). A tela só precisa:
//  - usar o campo novo no menu de plataformas, na barra de lojas ("Todas",
//    "Todas · Shopee", cada loja), no seletor de loja da tela estreita e na
//    aba Caixa; a API antiga (sem o campo) cai no "falta responder";
//  - explicar no title da loja "mensagens não lidas esperando resposta (como
//    no Duoke)", com o "Falta responder" (conversas, só o chat) ao lado;
//  - deixar a Caixa Humano como está (o número dela na aba, no menu e na
//    barra, e o title da loja com o texto de sempre);
//  - no title do Instagram, dizer que o Direct que passou da janela de 24 h
//    fica fora do número (continua no "Falta responder");
//  - na caixa Comentários com "Falta responder" (vazia: só o chat), apontar
//    o filtro Mídia;
//  - dizer no title da aba "Falta responder" que ela é só o chat;
//  - reler o /resumo quando o não lido da conversa aberta muda (marcou como lida);
//  - e o contrato com a API.
// Desde 15/10/2026 (o dono: "os números da Caixa têm que bater com o Duoke";
// o Eduardo: "1 mensagem na kia … a kia tá mostrando 9 naquela conversa no
// duoke") a régua do servidor é o NÃO LIDO DO PRÓPRIO DUOKE: as mensagens do
// cliente desde a última resposta de pessoa da loja ou desde que alguém abriu a
// conversa no DaVinci (`visto_em`), nas conversas de chat esperando resposta
// com o comprador nas últimas 24 h. A tela: o title da loja explica isso, e a
// página relê o /resumo quando o `visto_em` da conversa aberta muda.
// A BOLINHA DA LINHA (15/10/2026, a revisão — a conversa dos 9 da Kia ficava
// sem número na linha): na Caixa, o `numero_duoke` da conversa (a mesma
// régua), com ou sem o negrito; fora do chat (null), na Caixa Humano e com a
// API antiga, a bolinha de antes.
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
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return descriptor
}
function exportsDe(descriptor, req = require) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(req, mod, mod.exports)
  return mod.exports
}
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const menuSfc = sfc('../components/AtendimentoFiltroPlataforma.vue')
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
const listaSfc = sfc('../components/AtendimentoLista.vue')
const F = exportsDe(menuSfc)
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const requerer = (n) => {
  if (n === '~/components/AtendimentoFiltroPlataforma.vue') return F
  if (n === '~/components/AtendimentoPlataforma.vue') return P
  if (n === 'lucide-vue-next') return new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-lucide': String(nome) }) }) })
  return require(n)
}
const LJ = exportsDe(lojasSfc, requerer)
const ICONE = { props: ['plataforma'], render() { return Vue.h('i', { 'data-icone': this.plataforma }) } }

async function renderizar(descriptor, props) {
  const { content } = compileScript(descriptor, { id: 'teste', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.component('AtendimentoIconePlataforma', ICONE)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
function filtros(o = {}) {
  return { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', ...o }
}

// O /resumo de 14/10/2026 13:47, resumido: a ATV com 3 conversas no "Falta
// responder" (só o chat; antes eram 9, com as avaliações antigas) e 5
// mensagens não lidas esperando resposta — o 5 do Duoke.
const RESUMO = {
  plataformas: [
    { plataforma: 'shopee', aguardando: 4, vencendo: 0, vencidas: 0, nao_lidas_duoke: 6, humano: 2 },
    { plataforma: 'ml', aguardando: 3, vencendo: 0, vencidas: 0, nao_lidas_duoke: 4, humano: 1 },
    { plataforma: 'tiktok', aguardando: 1, vencendo: 0, vencidas: 0, nao_lidas_duoke: 0, humano: 0 },
    { plataforma: 'site', aguardando: 0, vencendo: 0, vencidas: 0, nao_lidas_duoke: 0, humano: 0 },
  ],
  lojas: [
    { integration_id: 'i-atv', plataforma: 'shopee', conta: 'ATV', aguardando: 3, vencidas: 0, nao_lidas: 7, nao_lidas_duoke: 5, humano: 2 },
    { integration_id: 'i-ag', plataforma: 'shopee', conta: 'Aguiar', aguardando: 1, vencidas: 0, nao_lidas: 1, nao_lidas_duoke: 1, humano: 0 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy', aguardando: 3, vencidas: 0, nao_lidas: 9, nao_lidas_duoke: 4, humano: 1 },
    { integration_id: 'i-tt', plataforma: 'tiktok', conta: 'Inova', aguardando: 1, vencidas: 0, nao_lidas: 0, nao_lidas_duoke: 0, humano: 0 },
    { integration_id: null, plataforma: 'site', externo_ref: 'site:charlots', conta: 'Charlots', aguardando: 0, vencidas: 0, nao_lidas_duoke: 0, humano: 0 },
  ],
  canais: [],
  flags: { humano_modos: null },
  humano: 3,
  nao_lidas_duoke: 10,
}
// A API antiga: sem o campo novo em lugar nenhum.
const semCampo = (o) => {
  const { nao_lidas_duoke: _, ...resto } = o
  return resto
}
const ANTIGO = { ...semCampo(RESUMO), plataformas: RESUMO.plataformas.map(semCampo), lojas: RESUMO.lojas.map(semCampo) }

// ------------------------------------------------ funções puras: o campo e a queda para a API antiga
{
  const sh = RESUMO.plataformas[0]
  assert.equal(F.numeroDe(sh, 'nao_lidas_duoke'), 6)
  assert.equal(F.numeroDe(sh), 4, 'sem `numero`: o falta responder, como antes')
  assert.equal(F.numeroDe(sh, 'humano'), 2)
  assert.equal(F.numeroDe(semCampo(sh), 'nao_lidas_duoke'), 4, 'a API antiga cai no falta responder')
  assert.equal(F.numeroDe({ aguardando: 9, nao_lidas_duoke: 0 }, 'nao_lidas_duoke'), 0, 'zero é zero (não cai no falta responder)')
  assert.equal(F.numeroDe(null, 'nao_lidas_duoke'), 0)
  assert.equal(F.aguardandoDe(RESUMO, ['shopee', 'ml'], 'nao_lidas_duoke'), 10)
  assert.equal(F.aguardandoDe(RESUMO, ['shopee', 'ml']), 7)
  assert.equal(F.aguardandoDe(ANTIGO, ['shopee', 'ml'], 'nao_lidas_duoke'), 7)
  assert.equal(F.textoDoNumero('nao_lidas_duoke'), 'mensagem(ns) não lida(s) esperando resposta (como no Duoke)')
  assert.equal(F.textoDoNumero(), 'conversa(s) falta responder', 'o texto antigo continua para quem não passa `numero`')
  assert.equal(F.textoCurtoDoNumero('humano'), 'esperando uma pessoa')

  // O menu: o número do Duoke de cada plataforma; as que existem, as mesmas.
  const chips = F.chipsDoResumo(RESUMO, '', 'nao_lidas_duoke')
  assert.deepEqual(chips.map((c) => [c.valor, c.aguardando]), [['ml', 4], ['shopee', 6], ['tiktok', 0], ['site', 0]])
  assert.deepEqual(F.chipsDoResumo(RESUMO, '').map((c) => c.valor), chips.map((c) => c.valor))
  // Só o número do Duoke, sem loja e sem "falta responder": aparece (nenhum número some da tela).
  const soDuoke = { ...RESUMO, lojas: [], plataformas: [{ plataforma: 'temu', aguardando: 0, vencendo: 0, vencidas: 0, nao_lidas_duoke: 2 }] }
  assert.deepEqual(F.chipsDoResumo(soDuoke, '', 'nao_lidas_duoke').map((c) => [c.valor, c.aguardando]), [['temu', 2]])
  // A Caixa Humano NÃO muda: o número dela.
  assert.deepEqual(F.chipsDoResumo(RESUMO, '', 'humano').map((c) => [c.valor, c.aguardando]), [['ml', 1], ['shopee', 2], ['tiktok', 0], ['site', 0]])

  // A barra: o mesmo campo, por loja e no corte.
  assert.equal(LJ.campoDaBarra('nao_lidas_duoke'), 'nao_lidas_duoke')
  assert.equal(LJ.campoDaBarra('humano'), 'humano')
  assert.equal(LJ.campoDaBarra(undefined), 'aguardando')
  const atv = RESUMO.lojas[0]
  assert.equal(LJ.numeroDaLoja(atv, 'nao_lidas_duoke'), 5, 'a ATV: 5, como no Duoke')
  assert.equal(LJ.numeroDaLoja(atv, 'humano'), 2)
  assert.equal(LJ.numeroDaLoja(atv), 3)
  assert.equal(LJ.numeroDaLoja(semCampo(atv), 'nao_lidas_duoke'), 3, 'a API antiga: o falta responder')
  assert.equal(LJ.numeroDoCorte(RESUMO, ['shopee'], 'nao_lidas_duoke'), 6)
  assert.equal(LJ.numeroDoCorte(RESUMO, ['shopee'], 'humano'), 2)
  assert.equal(LJ.numeroDoCorte(RESUMO, ['shopee']), 4)
}

// ------------------------------------------------ renderizado: o menu e a barra
async function renderizados() {
  // O menu de plataformas na Caixa: "Todas 10" e o número do Duoke de cada uma.
  let html = await renderizar(menuSfc, { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke' })
  assert.match(html, /data-plataforma-botao[^>]*>[\s\S]*?Todas[\s\S]*?>10<[\s\S]*?<\/button>/)
  assert.match(html, /title="Todas as plataformas: 10 mensagem\(ns\) não lida\(s\) esperando resposta \(como no Duoke\)"/)
  const chips = (h) => [...h.matchAll(/data-chip="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  assert.deepEqual(chips(html), [['todas', 'Todas 10'], ['ml', 'Mercado Livre 4'], ['shopee', 'Shopee 6'], ['tiktok', 'TikTok'], ['site', 'Sites']])
  assert.match(html, /aria-label="Só Shopee — 6 não lida\(s\) esperando resposta" title="Só Shopee: 6 mensagem\(ns\) não lida\(s\) esperando resposta \(como no Duoke\)"/)
  // A API antiga: o falta responder.
  html = await renderizar(menuSfc, { resumo: ANTIGO, filtros: filtros(), numero: 'nao_lidas_duoke' })
  assert.deepEqual(chips(html)[0], ['todas', 'Todas 8'])
  // A Caixa Humano: o número dela.
  html = await renderizar(menuSfc, { resumo: RESUMO, filtros: filtros(), numero: 'humano' })
  assert.deepEqual(chips(html), [['todas', 'Todas 3'], ['ml', 'Mercado Livre 1'], ['shopee', 'Shopee 2'], ['tiktok', 'TikTok'], ['site', 'Sites']])

  // A barra de lojas na Caixa: a bolinha é o número do Duoke.
  const linhas = (h) => [...h.matchAll(/<button type="button" class="relative mx-1[^"]*"[^>]*title="([^"]*)"[^>]*>([\s\S]*?)<\/button>/g)]
    .map((m) => [m[1].split('\n')[0], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  html = await renderizar(lojasSfc, { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke' })
  assert.deepEqual(linhas(html), [
    ['Todas as lojas — 10 mensagem(ns) não lida(s) esperando resposta (como no Duoke)', 'Todas 10'],
    ['Shopee · Aguiar', 'Aguiar 1'],
    ['Shopee · ATV', 'ATV 5'],
    ['TikTok · Inova', 'Inova'],
    ['Mercado Livre · Poofy', 'Poofy 4'],
    ['Site · Charlots', 'Charlots'],
  ])
  // O title da loja explica o número e mantém o "Falta responder" (conversas, só o chat).
  const tituloAtv = (html.match(/title="(Shopee · ATV[^"]*)"/) || [])[1] || ''
  assert.match(tituloAtv, /\n5 mensagem\(ns\) do cliente desde a última resposta ou desde que alguém abriu a conversa — como no Duoke\n3 conversa\(s\) no Falta responder \(só o chat\)\n/)
  assert.match(tituloAtv, /\n7 não lida\(s\) segundo a plataforma/)
  // O cabeçalho do grupo soma as lojas dele.
  assert.match(html, /data-grupo="shopee"[^>]*>[\s\S]*?>6<\/span>/)
  // Cortada na Shopee: "Todas · Shopee" com o número do Duoke da plataforma.
  html = await renderizar(lojasSfc, { resumo: RESUMO, filtros: filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }), numero: 'nao_lidas_duoke' })
  assert.match(html, /title="Todas as lojas Shopee — 6 mensagem\(ns\) não lida\(s\) esperando resposta \(como no Duoke\)\n/)
  assert.match(html, /Todas · Shopee<\/span><span[^>]*>6<\/span>/)
  // Recolhida: o title do grupo (a etiqueta curta) diz o mesmo.
  html = await renderizar(lojasSfc, { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', recolhida: true })
  assert.match(html, /title="Só Shopee: todas as lojas Shopee — 6 mensagem\(ns\) não lida\(s\) esperando resposta \(como no Duoke\)"/)
  // A API antiga: a bolinha cai no falta responder.
  html = await renderizar(lojasSfc, { resumo: ANTIGO, filtros: filtros(), numero: 'nao_lidas_duoke' })
  assert.deepEqual(linhas(html).slice(1, 3), [['Shopee · Aguiar', 'Aguiar 1'], ['Shopee · ATV', 'ATV 3']])
  // A Caixa Humano NÃO muda: a bolinha dela.
  html = await renderizar(lojasSfc, { resumo: RESUMO, filtros: filtros(), numero: 'humano' })
  assert.deepEqual(linhas(html).map((l) => l[1]), ['Todas 3', 'Aguiar', 'ATV 2', 'Inova', 'Poofy 1', 'Charlots'])
  assert.match(html, /title="Todas as lojas — 3 esperando uma pessoa \(a IA não pode responder\)"/)
  assert.doesNotMatch(html, /como no Duoke/, 'a Caixa Humano não fala do número do Duoke')
  // ...e o title da loja é o de sempre ("N conversa(s) esperando resposta").
  const tituloHumano = (html.match(/title="(Shopee · ATV[^"]*)"/) || [])[1] || ''
  assert.match(tituloHumano, /^Shopee · ATV\n3 conversa\(s\) esperando resposta\n2 na Caixa Humano/)
  assert.doesNotMatch(tituloHumano, /Falta responder/)

  // O Instagram: o Direct que passou da janela de 24 h fica fora do número
  // do Duoke (a bolinha) e o title diz por quê; na Caixa Humano, nada disso.
  const comIg = {
    ...RESUMO,
    plataformas: [...RESUMO.plataformas, { plataforma: 'instagram', aguardando: 3, vencendo: 0, vencidas: 2, nao_lidas_duoke: 1, humano: 0 }],
    lojas: [...RESUMO.lojas, { integration_id: null, plataforma: 'instagram', rede_social_id: 'rs-ch', externo_ref: 'rede:instagram:1', conta: '@charlots_br', aguardando: 3, vencidas: 2, direct_aguardando: 3, direct_total: 5, nao_lidas_duoke: 1, humano: 0 }],
  }
  html = await renderizar(lojasSfc, { resumo: comIg, filtros: filtros(), numero: 'nao_lidas_duoke' })
  assert.deepEqual(linhas(html).find((l) => l[0] === 'Instagram · @charlots_br'), ['Instagram · @charlots_br', '@charlots_br 1'])
  const tituloIg = (html.match(/title="(Instagram · @charlots_br[^"]*)"/) || [])[1] || ''
  assert.match(tituloIg, /\nDirect: 3 esperando \(só leitura\) — 2 passou\(aram\) da janela de 24 h: fora do número, no Falta responder/)
  html = await renderizar(lojasSfc, { resumo: comIg, filtros: filtros(), numero: 'humano' })
  assert.doesNotMatch((html.match(/title="(Instagram · @charlots_br[^"]*)"/) || [])[1] || '', /janela/)
}

// ------------------------------------------------ a página, a lista e o contrato
{
  const pagina = web('pages/atendimento.vue')
  // O menu e a barra: o número do Duoke na Caixa, o da Caixa Humano nela.
  assert.match(pagina, /<AtendimentoFiltroPlataforma v-if="naCaixa" v-model:filtros="filtros" :resumo="resumo" :numero="emHumano \? 'humano' : 'nao_lidas_duoke'" \/>/)
  assert.match(pagina, /<AtendimentoLojas v-model:filtros="filtros" v-model:recolhida="lojasRecolhidas" :resumo="resumo" :numero="emHumano \? 'humano' : 'nao_lidas_duoke'" \/>/)
  // A aba Caixa: o total do Duoke, vermelho (a API antiga não manda: sem número).
  assert.match(pagina, /const naoLidasDuoke = computed\(\(\) => Number\(resumo\.value\?\.nao_lidas_duoke\) \|\| 0\)/)
  assert.match(pagina, /v-if="a\.value === 'caixa' && naoLidasDuoke"[\s\S]{0,600}data-marca-caixa\s*>\{\{ naoLidasDuoke > 99 \? '99\+' : naoLidasDuoke \}\}<\/span>/)
  assert.match(pagina, /:title="`\$\{naoLidasDuoke\} mensagem\(ns\) não lida\(s\) esperando resposta \(como no Duoke\)/)
  // A aba Caixa Humano continua com o número dela.
  assert.match(pagina, /v-if="a\.value === 'humano' && resumo\?\.humano"[\s\S]{0,500}data-marca-humano\s*>\{\{ resumo\.humano > 99 \? '99\+' : resumo\.humano \}\}<\/span>/)
  // Marcou como lida ao abrir (o não lido mudou): relê o /resumo na hora.
  const aoMudar = (pagina.match(/function aoMudarConversa\([\s\S]*?\n}\n/) || [])[0] || ''
  assert.match(aoMudar, /c\.nao_lidas !== undefined && \(Number\(antes\.nao_lidas\) \|\| 0\) !== \(Number\(c\.nao_lidas\) \|\| 0\)/)
  // O número da linha mudou (chegou mensagem, abriu): relê também.
  assert.match(aoMudar, /c\.numero_duoke !== undefined && \(antes\.numero_duoke \?\? null\) !== \(c\.numero_duoke \?\? null\)/)
  // Abriu a conversa (o "visto no DaVinci" mudou, o número dela zera): idem.
  assert.match(aoMudar, /c\.visto_em !== undefined && \(antes\.visto_em \?\? null\) !== \(c\.visto_em \?\? null\)/)
  // E roda de verdade: a linha recebe o visto → relê; o mesmo visto → não relê.
  {
    const corpo = aoMudar.replace(/^function aoMudarConversa\(c: ConversaResumo\) \{/, '').replace(/\n}\n$/, '')
    const rodar = (antes, c) => {
      let releu = 0
      const itens = { value: [antes] }
      new Function('c', 'itens', 'selecionada', 'emHumano', 'carregarResumo', 'linkDoChat', 'abrirConversaDoEmail', transpile(corpo))(
        c, itens, { value: antes.id }, { value: false }, () => { releu++ }, null, () => {},
      )
      return releu
    }
    const linha = { id: 'k1', aguardando_resposta: true, nao_lidas: 0, visto_em: null }
    assert.equal(rodar(linha, { ...linha, visto_em: '2026-10-15T10:00:00+00:00' }), 1)
    assert.equal(rodar({ ...linha, visto_em: '2026-10-15T10:00:00+00:00' }, { ...linha, visto_em: '2026-10-15T10:00:00+00:00' }), 0)
    assert.equal(rodar(linha, { id: 'k1', aguardando_resposta: true }), 0, 'a API antiga (sem o campo): nada')
  }

  // A lista: a aba "Falta responder" é só o chat (o title diz), com o número
  // do /resumo (`aguardando`); o seletor de loja da tela estreita mostra o
  // mesmo número da barra.
  const L = exportsDe(listaSfc, requerer)
  const aba = L.ABAS_LISTA.find((a) => a.value === 'aguardando')
  assert.match(aba.hint, /conversas de chat/)
  assert.match(aba.hint, /avaliação, reclamação\/devolução, carrinho e comentários ficam nos filtros próprios/)
  const lista = web('components/AtendimentoLista.vue')
  assert.match(lista, /v-if="a\.value === 'aguardando' && contagem\.aguardando"/)
  assert.match(lista, /return numeroDe\(l, emHumano\.value \? 'humano' : 'nao_lidas_duoke'\)/)
  assert.match(lista, /<template v-if="numeroNoSeletor\(l\)"> \(\{\{ numeroNoSeletor\(l\) \}\}\)<\/template>/)
  // A caixa Comentários com "Falta responder": vazia (só o chat) — o vazio
  // aponta o filtro Mídia, com o botão; nos outros, o vazio de sempre.
  const trecho = lista.slice(lista.indexOf('const VAZIO'), lista.indexOf('// ↑/↓ com o foco'))
  const vazio = (f) => new Function('computed', 'filtros', `${transpile(trecho)}\nreturn textoVazio`)(Vue.computed, Vue.ref({ q: '', canal: '', ...f })).value
  assert.equal(vazio({ filtro: 'aguardando', canal: 'comentario' }), 'Os comentários não entram no "Falta responder" (só o chat): os que esperam a resposta da marca ficam no filtro Mídia.')
  assert.equal(vazio({ filtro: 'aguardando', canal: 'dm' }), 'Nada esperando resposta agora.')
  assert.equal(vazio({ filtro: 'midia', canal: 'comentario' }), 'Nenhuma conversa com esses filtros.')
  assert.match(lista, /<div v-if="!emHumano && comentariosForaDoFalta" class="mt-2">\s*<button type="button" class="text-xs underline" data-ver-midia @click="mudar\('filtro', 'midia'\)">ver no filtro Mídia<\/button>/)
  assert.match(lista, /title="pergunta sem resposta da marca: vem primeiro no filtro Mídia"/)
  const tipos = web('components/AtendimentoPlataforma.vue')
  assert.equal((tipos.match(/\n {2}nao_lidas_duoke\?: number\n/g) || []).length, 3, 'plataforma, loja e o total')

  // O contrato com a API.
  const rota = api('routers/atendimento.py')
  const resumo = (rota.match(/async def resumo\([\s\S]*?\n\nasync def _leitura_parada/) || [])[0]
  assert.ok(resumo, 'o /resumo existe')
  assert.match(resumo, /duoke = func\.coalesce\(func\.sum\(pendente\.nao_lidas_duoke_sql\(agora=agora\)\), 0\)/)
  // Calculado só por loja (a revisão: +33 a +72 ms por SUM); a plataforma é a soma das lojas.
  assert.equal((resumo.match(/\n {8}duoke,\n/g) || []).length, 1, 'só na q_loja')
  assert.match(resumo, /for p in plataformas:\n {8}p\.nao_lidas_duoke \+= duoke_da_plataforma\.get\(p\.plataforma, 0\)/)
  // A linha (lista e detalhe) leva o número dela pela mesma régua.
  assert.match(rota, /pendente\.nao_lidas_duoke_sql\(agora=agora\)\.label\("numero_duoke"\)/)
  assert.match(rota, /numero_duoke=numero or 0,/, 'o detalhe (e as ações) também')
  assert.match(rota, /if numero_duoke is not None and pendente\.do_chat\(c\)/, 'fora do chat: None')
  assert.match(api('schemas/atendimento.py'), /^ {4}numero_duoke: int \| None = None$/m)
  assert.match(api('services/atendimento/instagram.py'), /"numero_duoke": int\(/, 'o Direct: o mesmo 1 na linha')
  assert.match(web('components/AtendimentoPlataforma.vue'), /^ {2}numero_duoke\?: number \| null$/m)
  assert.match(resumo, /nao_lidas_duoke=sum\(p\.nao_lidas_duoke for p in plataformas\)/, 'o total (a aba Caixa)')
  assert.match(resumo, /p_ig\.nao_lidas_duoke \+= instagram\.no_numero_do_duoke\(ig\)/, 'o Direct: 1 por conversa esperando, na janela')
  assert.match(rota, /alvo\.nao_lidas_duoke \+= instagram\.no_numero_do_duoke\(d\)/, 'e na linha da conta')
  assert.match(api('services/atendimento/instagram.py'), /def no_numero_do_duoke\(numeros: dict\) -> int:[\s\S]*?return max\(0, int\(numeros\.get\("aguardando"\) or 0\) - int\(numeros\.get\("vencidas"\) or 0\)\)/)
  assert.match(resumo, /falta = pendente\.falta_responder_no_chat_sql\(\)/)
  assert.match(rota, /alvo\.nao_lidas_duoke \+= lj\.nao_lidas_duoke/, 'as caixas do mesmo site somam')
  const schemas = api('schemas/atendimento.py')
  assert.equal((schemas.match(/\n {4}nao_lidas_duoke: int = 0\n/g) || []).length, 3)
  const regua = api('services/atendimento/pendente.py')
  assert.match(regua, /CANAIS_FORA_DO_CHAT: frozenset\[str\] = frozenset\(\n {4}\{CANAL_AVALIACAO, CANAL_RECLAMACAO, CANAL_COMENTARIO, CANAL_CARRINHO\}\n\)/)
  assert.match(regua, /def nao_lidas_duoke_sql\(/)
  assert.match(regua, /c\.aguardando_resposta\.is_\(True\)/, 'a vez do comprador na FILA (a automática não fecha)')
  // A régua de 15/10/2026: o não lido do Duoke — a mensagem do cliente depois
  // da última resposta de PESSOA ou da abertura no DaVinci, na janela de 24 h.
  assert.match(regua, /^JANELA_DO_NUMERO = timedelta\(hours=24\)$/m)
  const sql = regua.slice(regua.indexOf('def nao_lidas_duoke_sql('))
  // Dois relógios: a resposta de pessoa no da plataforma; o visto na CHEGADA (`created_at`).
  assert.match(sql, /or_\(c\.ultima_da_loja_em\.is_\(None\), momento > c\.ultima_da_loja_em\)/)
  assert.match(sql, /or_\(c\.visto_em\.is_\(None\), m\.created_at > c\.visto_em\)/)
  assert.doesNotMatch(sql, /greatest/, 'o visto não se compara com o horário da plataforma')
  assert.match(sql, /m\.autor == AUTOR_CLIENTE/)
  assert.match(sql, /c\.ultima_do_cliente_em > limite/)
  assert.doesNotMatch(sql, /c\.nao_lidas\b/, 'o não lido da plataforma não entra no número')
}

// A bolinha da linha (15/10/2026): o número da conversa na Caixa.
{
  const L = exportsDe(listaSfc, requerer)
  const linha = (x) => ({ aguardando_resposta: true, nao_lidas: 0, pendentes: 0, ...x })
  // A conversa dos 9 da Kia: a automática por último (fora do "Falta responder",
  // sem negrito, a Shopee zerou o não lido) — mostra o 9.
  const kia = linha({ falta_responder: false, pendentes: 9, numero_duoke: 9 })
  assert.equal(L.linhaMarcada(kia), false, 'sem negrito (para o Duoke, respondeu)')
  assert.equal(L.bolinhaDaLinha(kia), 9, 'e o número na bolinha, como no Duoke')
  // Aberta no DaVinci: em negrito (ninguém respondeu), sem bolinha.
  const aberta = linha({ falta_responder: true, pendentes: 3, nao_lidas: 3, numero_duoke: 0 })
  assert.equal(L.linhaMarcada(aberta), true)
  assert.equal(L.bolinhaDaLinha(aberta), 0)
  // Fora do chat (null) e a API antiga (sem o campo): a bolinha de antes.
  const avaliacao = linha({ falta_responder: true, pendentes: 1, numero_duoke: null })
  assert.equal(L.bolinhaDaLinha(avaliacao), 1)
  assert.equal(L.bolinhaDaLinha(linha({ falta_responder: false, pendentes: 2, numero_duoke: null })), 0)
  assert.equal(L.bolinhaDaLinha(linha({ falta_responder: true, pendentes: 4, nao_lidas: 1 })), 4)
  // A Caixa Humano: pela fila, como sempre (o número não entra).
  assert.equal(L.bolinhaDaLinha(kia, true), 9)
  assert.equal(L.bolinhaDaLinha(aberta, true), 3)
  // A soma das bolinhas das linhas de uma loja é o número dela (o servidor manda a mesma régua).
  assert.equal([kia, aberta, linha({ falta_responder: true, numero_duoke: 2 })].reduce((n, c) => n + L.bolinhaDaLinha(c), 0), 11)
  assert.match(web('components/AtendimentoLista.vue'), /:nao-lidas="bolinhaDaLinha\(c, emHumano\)"/)
}

renderizados().then(
  () => console.log('ok — atendimento-numeros-duoke'),
  (e) => { console.error(e); process.exit(1) },
)
