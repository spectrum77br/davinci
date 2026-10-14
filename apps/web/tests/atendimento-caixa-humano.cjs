// node tests/atendimento-caixa-humano.cjs — a CAIXA HUMANO na tela (09/10/2026).
// O Eduardo: "criaremos ao lado da Caixa uma Caixa Humano, que virá quando a
// IA não puder responder a questão [...] só as que falta responder por um
// humano que a IA não conseguiu". Quem decide é o backend
// (services/atendimento/humano.py, `?caixa=humano`); aqui, a tela:
//  - o contrato com a API (HumanoOut, os números do /resumo, a flag, os
//    rótulos iguais aos de constantes.ROTULO_HUMANO, o `?caixa=`);
//  - as funções puras (os motivos escritos, o chip, o menu Filtrar e o
//    filtro em que a lista abre);
//  - a página: a aba logo depois da Caixa com o número, as mesmas colunas,
//    `caixa=humano` na consulta, a troca de aba (recarrega a lista no filtro
//    da aba), a linha que sai na hora quando alguém responde;
//  - a lista renderizada (Vue SSR, setup de verdade): o título no lugar das
//    abas, o chip do motivo (também na Caixa), o vazio;
//  - a barra de lojas e o menu do topo com os números da Caixa Humano;
//  - a faixa na conversa;
//  - SÓ AS LOJAS NO AUTOMÁTICO (14/10/2026, o dono: "a Caixa Humano só para
//    as lojas que estão automáticas"): o contrato `flags.humano_modos`, o
//    texto do topo, o vazio sem loja no modo — nenhuma, a loja ou a
//    plataforma escolhida — (com "ver em Lojas e modo"), a loja fora do modo
//    na barra (sem bolinha, o title diz por quê) e o chip/faixa "Precisa de
//    pessoa" na conversa de loja fora (`humano.na_caixa` false).
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
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const plataformaSfc = sfc('../components/AtendimentoPlataforma.vue')
const filtroSfc = sfc('../components/AtendimentoFiltroPlataforma.vue')
const listaSfc = sfc('../components/AtendimentoLista.vue')
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
const etiquetaSfc = sfc('../components/AtendimentoEtiqueta.vue')
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const paginaSfc = sfc('../pages/atendimento.vue')

// Os <script> (sem setup) de cada um, com os imports entre eles.
const modulos = {}
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-lucide': String(nome) }) }) })
function requerer(n) {
  if (n === 'lucide-vue-next') return icones
  if (n === '@vueuse/core') return { onClickOutside: () => {} }
  if (n.startsWith('~/components/')) return modulos[n]
  return require(n)
}
function carregar(nome, s) {
  const mod = { exports: {} }
  if (s.descriptor.script) new Function('require', 'module', 'exports', transpile(s.descriptor.script.content))(requerer, mod, mod.exports)
  modulos[`~/components/${nome}.vue`] = mod.exports
  return mod.exports
}
const P = carregar('AtendimentoPlataforma', plataformaSfc)
const F = carregar('AtendimentoFiltroPlataforma', filtroSfc)
const L = carregar('AtendimentoLista', listaSfc)
carregar('AtendimentoEtiqueta', etiquetaSfc)
const Lj = carregar('AtendimentoLojas', lojasSfc)

function filtros(o = {}) {
  return { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', tipo_chamado: '', plataforma_topo: '', ...o }
}
const HUM = (motivos, extra = {}) => ({
  motivos,
  rotulos: motivos.map((m) => P.ROTULO_HUMANO[m]),
  assuntos: [],
  assuntos_rotulos: [],
  ...extra,
})

// ------------------------------------------------ o contrato com a API
{
  const schemas = api('schemas/atendimento.py')
  const bloco = (nome) => (schemas.match(new RegExp(`^class ${nome}\\(BaseModel\\):([\\s\\S]*?)^class `, 'm')) || [])[1] || ''
  // HumanoOut = o tipo da tela, campo a campo.
  const campos = [...bloco('HumanoOut').matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1])
  const tipo = (plataformaSfc.descriptor.script.content.match(/export type HumanoConversa = \{([\s\S]*?)\n\}/) || [])[1] || ''
  const chavesTs = [...tipo.matchAll(/^ {2}([a-z_]+)\??: /gm)].map((m) => m[1])
  // `na_caixa` (14/10/2026): a loja nos modos da Caixa Humano; True por padrão (a API antiga).
  assert.deepEqual(campos, ['motivos', 'rotulos', 'assuntos', 'assuntos_rotulos', 'na_caixa'])
  assert.match(bloco('HumanoOut'), /^ {4}na_caixa: bool = True$/m)
  assert.deepEqual(chavesTs, campos, 'o tipo da tela = o HumanoOut')
  assert.match(bloco('ConversaResumoOut'), /^ {4}humano: HumanoOut \| None = None$/m)
  for (const c of ['PlataformaResumoOut', 'LojaResumoOut', 'ResumoOut']) assert.match(bloco(c), /^ {4}humano: int = 0$/m, c)
  assert.match(bloco('FlagsOut'), /^ {4}humano_ativa: bool = False$/m)
  // Os modos da Caixa Humano (14/10/2026): a lista, ou None = toda loja.
  assert.match(bloco('FlagsOut'), /^ {4}humano_modos: list\[str\] \| None = None$/m)
  const tipos = plataformaSfc.descriptor.script.content
  for (const t of ['ResumoPlataforma', 'ResumoLoja', 'Resumo']) {
    const corpo = (tipos.match(new RegExp(`export type ${t} = \\{([\\s\\S]*?)\\n\\}`)) || [])[1] || ''
    assert.match(corpo, /^ {2}humano\?: number$/m, t)
  }
  assert.match(tipos, /^ {2}humano\?: HumanoConversa \| null$/m)
  assert.match(tipos, /^ {2}humano_ativa\?: boolean$/m)
  assert.match(tipos, /^ {2}humano_modos\?: string\[\] \| null$/m)
  // O /resumo manda os modos que o servidor usa no filtro.
  assert.match(api('routers/atendimento.py'), /humano_modos=list\(modos_humano\) if modos_humano is not None else None,/)
  assert.match(api('routers/atendimento.py'), /modos_humano = humano_svc\.modos_da_caixa\(\)\n\s+lojas_humano = await humano_svc\.lojas_da_caixa\(session, modos_humano\)\n\s+na_caixa_humano = func\.count\(\)\.filter\(humano_svc\.caixa_humano_sql\(lojas_humano\)\)/)
  // O padrão do servidor é o Automático, sem precisar do .env.
  assert.match(api('services/atendimento/humano.py'), /^MODOS_PADRAO: tuple\[str, \.\.\.\] = \(MODO_AUTO,\)$/m)
  assert.match(api('config.py'), /^ {4}atendimento_humano_modos: str = ""$/m)

  // Os rótulos de reserva da tela = os da API (constantes.ROTULO_HUMANO).
  const constantes = api('services/atendimento/constantes.py')
  const codigos = Object.fromEntries([...constantes.matchAll(/^(HUMANO_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
  const rotulosPy = Object.fromEntries([...((constantes.match(/^ROTULO_HUMANO: dict\[str, str\] = \{([\s\S]*?)^\}/m) || [])[1] || '').matchAll(/^\s+(HUMANO_[A-Z_]+): "([^"]+)",$/gm)].map((m) => [codigos[m[1]], m[2]]))
  assert.ok(Object.keys(rotulosPy).length >= 16, 'achou o ROTULO_HUMANO')
  assert.deepEqual(P.ROTULO_HUMANO, rotulosPy)

  // A lista: `?caixa=humano` (o resto vira 422) e o filtro em que ela abre
  // vai pelo prazo; o menu só tem filtros que a API conhece.
  const rota = api('routers/atendimento.py')
  assert.match(rota, /^CAIXA_HUMANO = "humano"$/m)
  assert.match(rota, /caixa: Annotated\[str \| None, Query\(max_length=16\)\] = None,/)
  assert.match(rota, /raise HTTPException\(422, detail=\{"code": "caixa_invalida"\}\)/)
  assert.match(rota, /if caixa_humano:\n\s+# [^\n]*\n\s+consulta = consulta\.where\(humano_svc\.caixa_humano_sql\(lojas_humano\)\)/)
  const filtrosApi = [...((rota.match(/^FILTROS = \(([\s\S]*?)^\)/m) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
  for (const f of [...L.FILTROS_FORA_DA_HUMANO, L.filtroPadrao('humano')]) assert.ok(filtrosApi.includes(f), f)
  assert.ok(P.FILTROS_PELO_PRAZO.includes(L.filtroPadrao('humano')), 'a Caixa Humano abre pelo prazo')
}

// ------------------------------------------------ funções puras
{
  // Os motivos escritos, na ordem da API; o "assunto" diz quais.
  assert.deepEqual(P.rotulosHumano(HUM(['reclamacao', 'atendente'])), ['Reclamação aberta', 'Pediu atendente'])
  assert.deepEqual(
    P.rotulosHumano(HUM(['assunto'], { assuntos: ['garantia', 'reembolso'], assuntos_rotulos: ['Garantia', 'Reembolso'] })),
    ['Assunto só de pessoa: Garantia, Reembolso'],
  )
  // Sem o nome do assunto (API antiga): o nome que a tela conhece.
  assert.deepEqual(P.rotulosHumano(HUM(['assunto'], { assuntos: ['garantia'] })), ['Assunto só de pessoa: Garantia'])
  // Código novo sem rótulo da API: a reserva; nem a reserva conhece: o código.
  assert.deepEqual(P.rotulosHumano({ motivos: ['ia_pausada', 'novo_motivo'], rotulos: [], assuntos: [], assuntos_rotulos: [] }), ['IA pausada', 'novo_motivo'])
  assert.deepEqual(P.rotulosHumano({ motivos: [], rotulos: [], assuntos: [], assuntos_rotulos: [] }), ['Precisa de uma pessoa'])
  assert.deepEqual(P.rotulosHumano(null), [])
  // O chip: o primeiro, "+N" e todos no title.
  assert.deepEqual(P.chipHumano(HUM(['devolucao', 'assunto', 'ia'], { assuntos: ['troca_devolucao'], assuntos_rotulos: ['Troca / devolução'] })), {
    texto: 'Pedido com devolução',
    mais: 2,
    titulo: 'Caixa Humano — a IA não responde esta:\n• Pedido com devolução\n• Assunto só de pessoa: Troca / devolução\n• A IA marcou: precisa de pessoa',
  })
  assert.equal(P.chipHumano(null), null)
  // A loja fora dos modos (14/10/2026, `na_caixa: false`): ela não está na
  // Caixa Humano — o chip e a faixa dizem "Precisa de pessoa".
  assert.deepEqual(P.faixaHumano(HUM(['atendente'])), { nome: 'Caixa Humano', frase: 'a IA não responde esta' })
  assert.deepEqual(P.faixaHumano(HUM(['atendente'], { na_caixa: true })), { nome: 'Caixa Humano', frase: 'a IA não responde esta' })
  assert.deepEqual(P.faixaHumano(HUM(['atendente'], { na_caixa: false })), { nome: 'Precisa de pessoa', frase: 'a IA não responderia esta' })
  assert.equal(P.chipHumano(HUM(['atendente'], { na_caixa: false })).titulo, 'Precisa de pessoa — a IA não responderia esta:\n• Pediu atendente')

  // Só as lojas nos modos (14/10/2026): os modos do /resumo, o texto e a loja.
  assert.deepEqual(P.modosDaCaixaHumano({ humano_modos: ['auto'] }), ['auto'])
  assert.equal(P.modosDaCaixaHumano({ humano_modos: null }), null, '"todos": toda loja')
  assert.equal(P.modosDaCaixaHumano({}), null, 'API antiga: toda loja')
  assert.equal(P.modosDaCaixaHumano({ humano_modos: [] }), null)
  assert.equal(P.modosDaCaixaHumano(null), null)
  assert.equal(P.textoModosHumano(['auto']), 'no Automático')
  assert.equal(P.textoModosHumano(['auto', 'copiloto']), 'no Automático ou no Copiloto')
  const canais = [
    { id: 'k1', integration_id: 'i-kia', plataforma: 'shopee', canal: 'chat', modo: 'auto' },
    { id: 'k2', integration_id: 'i-ml', plataforma: 'ml', canal: 'pergunta', modo: 'observar' },
    { id: 'k3', integration_id: 'i-ml', plataforma: 'ml', canal: 'pos_venda', modo: 'auto' },
    { id: 'k4', integration_id: 'i-obs', plataforma: 'shopee', canal: 'chat', modo: 'observar' },
    { id: 'k5', integration_id: null, externo_ref: 'site:charlots', plataforma: 'site', canal: 'carrinho', modo: 'observar' },
  ]
  assert.equal(P.lojaNosModos(canais, { integration_id: 'i-kia' }, ['auto']), true)
  assert.equal(P.lojaNosModos(canais, { integration_id: 'i-ml' }, ['auto']), true, 'algum canal da loja no modo')
  assert.equal(P.lojaNosModos(canais, { integration_id: 'i-obs' }, ['auto']), false)
  assert.equal(P.lojaNosModos(canais, { integration_id: '', externo_ref: 'site:charlots' }, ['auto']), false)
  assert.equal(P.lojaNosModos(canais, { integration_id: '' }, ['auto']), null, 'sem loja (ou a do robô): não dá para saber')
  assert.equal(P.canaisNosModos(canais, ['auto']), 2)
  assert.equal(P.canaisNosModos(canais, ['copiloto']), 0)
  assert.equal(P.canaisNosModos(undefined, ['auto']), 0)
  // A plataforma escolhida: alguma loja dela no modo?
  assert.equal(P.plataformaNosModos(canais, ['shopee'], ['auto']), true)
  assert.equal(P.plataformaNosModos(canais, ['ml'], ['auto']), true)
  assert.equal(P.plataformaNosModos(canais, ['site'], ['auto']), false)
  assert.equal(P.plataformaNosModos(canais, ['tiktok'], ['auto']), false, 'sem loja nenhuma')
  assert.equal(P.plataformaNosModos(canais, ['instagram', 'facebook'], ['auto']), false)
  assert.equal(P.plataformaNosModos(canais, [], ['auto']), null, 'sem plataforma: não se aplica')

  // O menu Filtrar na Caixa Humano: sem o que nunca entra nela.
  assert.deepEqual(L.opcoesDoFiltrar('').map((f) => f.value), L.FILTROS_MENU.map((f) => f.value))
  const naHumano = L.opcoesDoFiltrar('humano').map((f) => f.value)
  for (const fora of ['fechadas', 'carrinho', 'midia']) assert.ok(!naHumano.includes(fora), fora)
  for (const fica of ['vencendo', 'vencidas', 'minhas', 'reclamacao', 'devolucao', 'ag_cancelamento', 'avaliacao', 'pos_venda']) assert.ok(naHumano.includes(fica), fica)
  assert.equal(L.filtroPadrao('humano'), 'aguardando')
  assert.equal(L.filtroPadrao(''), 'todas')
  assert.equal(L.filtroPadrao(undefined), 'todas')

  // Os números do menu do topo: o da Caixa Humano; as plataformas, as mesmas.
  const resumo = {
    plataformas: [
      { plataforma: 'shopee', aguardando: 10, vencendo: 0, vencidas: 0, humano: 4 },
      { plataforma: 'ml', aguardando: 3, vencendo: 0, vencidas: 0, humano: 0 },
      { plataforma: 'instagram', aguardando: 2, vencendo: 0, vencidas: 0 },
    ],
    lojas: [{ integration_id: 'i-sh', plataforma: 'shopee', conta: 'atv', aguardando: 10, vencidas: 0, humano: 4 }],
    canais: [],
    flags: {},
  }
  assert.equal(F.aguardandoDe(resumo, ['shopee']), 10)
  assert.equal(F.aguardandoDe(resumo, ['shopee'], 'humano'), 4)
  assert.equal(F.aguardandoDe(resumo, ['instagram', 'facebook'], 'humano'), 0, 'API sem o número: zero')
  const caixa = F.chipsDoResumo(resumo, '')
  const humano = F.chipsDoResumo(resumo, '', 'humano')
  assert.deepEqual(humano.map((c) => c.valor), caixa.map((c) => c.valor), 'as mesmas plataformas nas duas')
  assert.deepEqual(humano.map((c) => [c.valor, c.aguardando]), [['ml', 0], ['shopee', 4], ['instagram,facebook', 0]])
  assert.match(F.textoDoNumero('humano'), /esperando uma pessoa/)

  // Só as lojas ATIVADAS (14/10/2026, o dono: "mostre também só as que estão
  // ativadas … por cada lugar, separado Shopee, ML"): com `humano_modos`, o
  // menu do topo da Caixa Humano só tem plataforma com canal no Automático
  // (ou com conversa contada lá, ou a escolhida), e a barra só a loja no modo.
  const ativado = {
    ...resumo,
    canais: [
      { id: 'k1', integration_id: 'i-kia', plataforma: 'shopee', canal: 'chat', modo: 'auto' },
      { id: 'k2', integration_id: 'i-sh', plataforma: 'shopee', canal: 'chat', modo: 'observar' },
      { id: 'k3', integration_id: 'i-ml', plataforma: 'ml', canal: 'pos_venda', modo: 'observar' },
    ],
    flags: { humano_modos: ['auto'] },
  }
  assert.deepEqual(F.chipsDoResumo(ativado, '', 'humano').map((c) => c.valor), ['shopee'], 'só a plataforma com loja no Automático')
  assert.deepEqual(F.chipsDoResumo(ativado, 'ml', 'humano').map((c) => c.valor), ['ml', 'shopee'], 'a escolhida fica')
  assert.deepEqual(F.chipsDoResumo(ativado, '').map((c) => c.valor), caixa.map((c) => c.valor), 'a Caixa não muda')
  const comConversa = { ...ativado, plataformas: ativado.plataformas.map((p) => (p.plataforma === 'ml' ? { ...p, humano: 2 } : p)) }
  assert.ok(F.chipsDoResumo(comConversa, '', 'humano').some((c) => c.valor === 'ml'), 'com conversa contada lá, fica')
  const barra = [
    { integration_id: 'i-kia', plataforma: 'shopee', conta: 'Kia', humano: 3 },
    { integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV', humano: 0 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'jlas2', humano: 0 },
    { integration_id: 'i-ml2', plataforma: 'ml', conta: 'velha', humano: 1 },
    { integration_id: '', plataforma: 'temu', conta: 'robô', humano: 0 },
  ]
  assert.deepEqual(Lj.lojasDaCaixaHumano(barra, ativado.canais, ['auto']).map((l) => l.conta), ['Kia', 'velha'])
  assert.equal(Lj.lojasDaCaixaHumano(barra, ativado.canais, null).length, barra.length, 'sem modos: toda loja')
  assert.equal(F.textoDoNumero(), 'conversa(s) falta responder')
}

// ------------------------------------------------ a página
const pagina = paginaSfc.descriptor.scriptSetup.content
const paginaTpl = paginaSfc.descriptor.template.content
{
  // A aba logo depois da Caixa, com o ícone de pessoa; `?tab=humano` abre nela.
  const abas = [...(pagina.match(/const ABAS[\s\S]*?\n\]/) || [''])[0].matchAll(/value: '([a-z]+)', label: '([^']+)', icon: (\w+)/g)].map((m) => [m[1], m[2], m[3]])
  assert.deepEqual(abas.slice(0, 2), [['caixa', 'Caixa', 'Inbox'], ['humano', 'Caixa Humano', 'UserRound']])
  assert.match(pagina, /type Aba = 'caixa' \| 'humano' \|/)
  assert.match(pagina, /import \{[^}]*\bUserRound\b[^}]*\} from 'lucide-vue-next'/)
  // O número da aba: o `humano` do /resumo, vermelho.
  assert.match(paginaTpl, /v-if="a\.value === 'humano' && resumo\?\.humano"[\s\S]{0,500}data-marca-humano\s*>\{\{ resumo\.humano > 99 \? '99\+' : resumo\.humano \}\}<\/span>/)
  // As MESMAS colunas (lojas | lista | conversa) nas duas.
  assert.match(pagina, /const naCaixa = computed\(\(\) => aba\.value === 'caixa' \|\| aba\.value === 'humano'\)/)
  assert.match(paginaTpl, /<!-- Caixa: lojas \| fila \| conversa \+ pedido -->[\s\S]*?<div\s+v-show="naCaixa"\s+ref="caixaEl"/)
  assert.match(paginaTpl, /<AtendimentoLojas v-model:filtros="filtros" v-model:recolhida="lojasRecolhidas" :resumo="resumo" :numero="emHumano \? 'humano' : 'aguardando'" \/>/)
  assert.match(paginaTpl, /<AtendimentoLista\s+v-model:filtros="filtros"[\s\S]*?:caixa="emHumano \? 'humano' : ''"/)
  // O vazio sem loja no modo abre "Lojas e modo" (14/10/2026).
  assert.match(paginaTpl, /<AtendimentoLista\s+v-model:filtros="filtros"[^/]*?@abrir-aba="abrirAba"\s*\/>/)
  assert.match(paginaTpl, /:ativa="naCaixa"/)
  // A conversa aberta fica no link nas duas (e, desde 13/10/2026, a de e-mail na aba E-mail).
  assert.match(pagina, /const aberta = naCaixa\.value \? selecionada\.value : aba\.value === 'mail' \? emailConversa\.value : null\n  if \(aberta\) query\.conversa = aberta/)
  // Polling e altura valem nas duas.
  assert.match(pagina, /if \(!el \|\| !naCaixa\.value\) return/)
  assert.doesNotMatch(pagina, /aba\.value !== 'caixa'/)
}
{
  // A consulta (o trecho de verdade): `caixa=humano` só na Caixa Humano.
  const params = new Function('filtros', 'emHumano', 'tipoChamadoAtivo', 'LIMITE', `${trecho(pagina, 'function params(', 'async function carregarLista')}\nreturn params`)
  const consulta = (humano, f = {}) => Object.fromEntries(new URLSearchParams(params({ value: filtros(f) }, { value: humano }, L.tipoChamadoAtivo, 50)()))
  assert.deepEqual(consulta(true, { filtro: 'aguardando', plataforma: 'shopee', plataforma_topo: 'shopee' }), { plataforma: 'shopee', filtro: 'aguardando', caixa: 'humano', limite: '50' })
  assert.deepEqual(consulta(false, { plataforma: 'shopee' }), { plataforma: 'shopee', filtro: 'todas', limite: '50' })
}
{
  // Respondeu (o servidor manda `humano: null`): na Caixa Humano a linha sai
  // na hora; na Caixa, só perde o chip. As contagens são relidas.
  const fazer = (emHumano) => {
    const itens = Vue.ref([
      { id: 'a', aguardando_resposta: true, humano: HUM(['reclamacao']) },
      { id: 'b', aguardando_resposta: true, humano: HUM(['atendente']) },
    ])
    let relidas = 0
    const fn = new Function('itens', 'emHumano', 'carregarResumo', `${trecho(pagina, 'function aoMudarConversa', '// ─── respostas prontas')}\nreturn aoMudarConversa`)(
      itens, { value: emHumano }, () => { relidas++ },
    )
    return { itens, fn, relidas: () => relidas }
  }
  let x = fazer(true)
  x.fn({ id: 'a', aguardando_resposta: false, humano: null })
  assert.deepEqual(x.itens.value.map((c) => c.id), ['b'])
  assert.equal(x.relidas(), 1)
  // A conversa mudou sem sair (outra etiqueta, a IA pausada entra nos motivos): fica.
  x.fn({ id: 'b', aguardando_resposta: true, humano: HUM(['atendente', 'ia_pausada']) })
  assert.deepEqual(x.itens.value.map((c) => c.id), ['b'])
  assert.deepEqual(x.itens.value[0].humano.motivos, ['atendente', 'ia_pausada'])
  // API antiga (sem `humano` na conversa): nada sai.
  x.fn({ id: 'b', aguardando_resposta: true })
  assert.deepEqual(x.itens.value.map((c) => c.id), ['b'])
  x = fazer(false)
  x.fn({ id: 'a', aguardando_resposta: true, humano: null })
  assert.deepEqual(x.itens.value.map((c) => c.id), ['a', 'b'], 'na Caixa a linha fica')
  assert.equal(x.itens.value[0].humano, null)
  assert.equal(x.relidas(), 1, 'saiu da Caixa Humano: o número da aba é relido')
}
{
  // Trocar de aba (o watch de verdade): a mesma caixa só atualiza; a outra
  // recarrega do zero, no filtro da aba (plataforma, loja e busca ficam).
  const montar = (caixaInicial, f) => {
    let cb = null
    const chamadas = []
    const estado = {
      itens: Vue.ref([{ id: 'velha' }]),
      proximo: Vue.ref('cursor'),
      filtros: Vue.ref(filtros(f)),
    }
    const corpo = `let caixaDaLista = ${JSON.stringify(caixaInicial)}\n${trecho(pagina, '// Entrou na Caixa ou na Caixa Humano', 'async function atualizarTudo')}`
    new Function('watch', 'aba', 'itens', 'proximo', 'filtros', 'filtroPadraoDa', 'carregarResumo', 'atualizarLista', 'carregarLista', corpo)(
      (_, fn) => { cb = fn }, null, estado.itens, estado.proximo, estado.filtros,
      (a) => (a === 'humano' ? 'aguardando' : 'todas'),
      () => chamadas.push('resumo'), () => chamadas.push('atualizar'), () => chamadas.push('carregar'),
    )
    return { ...estado, ir: (a) => cb(a), chamadas }
  }
  // Caixa → Caixa Humano: lista zerada, filtro da aba (o watch dos filtros recarrega).
  let m = montar('caixa', { filtro: 'reclamacao', plataforma: 'shopee', q: 'rui' })
  m.ir('humano')
  assert.deepEqual(m.itens.value, [])
  assert.equal(m.proximo.value, null)
  assert.deepEqual(m.filtros.value, filtros({ filtro: 'aguardando', plataforma: 'shopee', q: 'rui' }))
  assert.deepEqual(m.chamadas, ['resumo'])
  // Já no filtro da aba: recarrega aqui.
  m = montar('caixa', { filtro: 'aguardando' })
  m.ir('humano')
  assert.deepEqual(m.chamadas, ['resumo', 'carregar'])
  // Voltou para a mesma (de "Lojas e modo"): só atualiza, o filtro fica.
  m = montar('humano', { filtro: 'vencidas' })
  m.ir('humano')
  assert.deepEqual(m.chamadas, ['resumo', 'atualizar'])
  assert.equal(m.filtros.value.filtro, 'vencidas')
  assert.deepEqual(m.itens.value, [{ id: 'velha' }])
  // Outras abas: nada.
  m = montar('caixa', {})
  m.ir('manual')
  assert.deepEqual(m.chamadas, [])
  // A lista abre no filtro da aba (`?tab=humano` direto).
  assert.match(pagina, /filtro: filtroPadraoDa\(aba\.value\)/)
  assert.match(pagina, /caixaDaLista = emHumano\.value \? 'humano' : 'caixa'/)
}

// ------------------------------------------------ a lista renderizada (setup de verdade)
async function renderizarLista(props) {
  const { content } = compileScript(listaSfc.descriptor, { id: 'lista', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick, onBeforeUnmount: Vue.onBeforeUnmount }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, {
    itens: [], carregando: false, carregandoMais: false, erro: null, temMais: false, selecionada: null, agora: Date.parse('2026-10-09T12:00:00Z'), meuId: null, ...props,
  })
  app.component('AtendimentoAvatar', { render: () => Vue.h('span', { 'data-avatar': '' }) })
  app.component('AtendimentoEtiqueta', { render: () => Vue.h('span', { 'data-etiqueta-stub': '' }) })
  app.component('AtendimentoAgCancelamentoLista', { render: () => null })
  app.config.warnHandler = () => {}
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
const RESUMO_LISTA = {
  humano: 5,
  plataformas: [
    { plataforma: 'shopee', aguardando: 9, vencendo: 0, vencidas: 0, humano: 4 },
    { plataforma: 'ml', aguardando: 2, vencendo: 0, vencidas: 0, humano: 1 },
  ],
  lojas: [
    { integration_id: 'i-sh', plataforma: 'shopee', conta: 'atv', aguardando: 9, vencidas: 0, humano: 4 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'poofy', aguardando: 2, vencidas: 0, humano: 1 },
  ],
  canais: [],
  flags: { humano_ativa: true },
}
const conversa = (id, o = {}) => ({
  id, plataforma: 'shopee', canal: 'chat', conta: 'atv', integration_id: 'i-sh', comprador_nome: `Comprador ${id}`, pedido_marketplace: null,
  anuncio_titulo: null, ultima_mensagem_em: '2026-10-09T11:00:00Z', ultima_mensagem_resumo: 'oi', ultima_autor: 'comprador', aguardando_resposta: true,
  prazo_resposta_em: null, situacao: 'aberta', nao_lidas: 0, tem_rascunho: false, atribuido_a: null, atribuido_a_nome: null, ia_pausada: false,
  sem_resposta_necessaria: false, somente_leitura: false, ...o,
})
async function lista() {
  const itens = [
    conversa('h1', { humano: HUM(['reclamacao', 'atendente', 'assunto'], { assuntos: ['garantia'], assuntos_rotulos: ['Garantia'] }) }),
    conversa('h2', { humano: HUM(['assunto'], { assuntos: ['reembolso'], assuntos_rotulos: ['Reembolso'] }) }),
  ]
  // Na Caixa Humano: o título com o total no lugar das abas, a explicação, o chip.
  let html = await renderizarLista({ itens, resumo: RESUMO_LISTA, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /data-titulo-humano[^>]*>[\s\S]*?Caixa Humano<\/span><span[^>]*>5<\/span>/)
  assert.doesNotMatch(html, /role="tablist"/, 'sem as abas Todas/Falta responder')
  assert.match(html, /data-dica-humano[^>]*>\s*Só o que a IA não pode responder e falta responder — respondeu, sai daqui\./)
  assert.doesNotMatch(html, /data-humano-desligada/)
  const chips = [...html.matchAll(/data-selo-humano[^>]*>([\s\S]*?)<\/span>\s*(?:<span data-etiqueta-stub|<\/span>)/g)]
  assert.equal(chips.length, 2)
  assert.match(html, /title="Caixa Humano — a IA não responde esta:\n• Reclamação aberta\n• Pediu atendente\n• Assunto só de pessoa: Garantia" data-selo-humano><i data-lucide="UserRound"[^>]*><\/i><span class="truncate">Reclamação aberta<\/span><span class="shrink-0 opacity-75">\+2<\/span>/)
  assert.match(html, /data-selo-humano><i data-lucide="UserRound"[^>]*><\/i><span class="truncate">Assunto só de pessoa: Reembolso<\/span><\/span>/, 'um motivo só: sem "+N"')
  // Com a loja escolhida: o número da loja.
  html = await renderizarLista({ itens, resumo: RESUMO_LISTA, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'ml', integration_id: 'i-ml' }) })
  assert.match(html, /Caixa Humano<\/span><span[^>]*>1<\/span>/)
  // Com a plataforma: o da plataforma.
  html = await renderizarLista({ itens, resumo: RESUMO_LISTA, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'shopee' }) })
  assert.match(html, /Caixa Humano<\/span><span[^>]*>4<\/span>/)
  // A triagem desligada no servidor: o aviso.
  html = await renderizarLista({ itens, resumo: { ...RESUMO_LISTA, flags: { humano_ativa: false } }, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /data-humano-desligada[^>]*>\s*A triagem está desligada no servidor: aqui só aparecem as conversas com a IA pausada\./)
  // Vazia, sem filtro: a boa notícia (sem "ver todas").
  html = await renderizarLista({ itens: [], resumo: RESUMO_LISTA, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /Nada esperando uma pessoa agora — o que a IA não puder responder aparece aqui\./)
  assert.doesNotMatch(html, />ver todas<|>tirar o filtro</)
  // Vazia com um filtro do menu: tirar o filtro volta para "Falta responder".
  html = await renderizarLista({ itens: [], resumo: RESUMO_LISTA, caixa: 'humano', filtros: filtros({ filtro: 'vencidas' }) })
  assert.match(html, /Nenhuma conversa na Caixa Humano com esses filtros\./)
  assert.match(html, />tirar o filtro</)
  assert.match(html, /aria-label="tirar o filtro"/)

  // Na Caixa: as abas de sempre, e a linha que está na Caixa Humano leva o chip.
  html = await renderizarLista({ itens: [conversa('c1'), itens[0]], resumo: RESUMO_LISTA, filtros: filtros() })
  assert.match(html, /role="tablist"/)
  assert.doesNotMatch(html, /data-titulo-humano|data-dica-humano/)
  assert.equal((html.match(/data-selo-humano/g) || []).length, 1, 'só a que está na Caixa Humano')
  // API antiga (sem `humano`): nada quebra, sem chip.
  html = await renderizarLista({ itens: [conversa('c1')], resumo: { ...RESUMO_LISTA, humano: undefined }, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /Caixa Humano<\/span><\/div>/, 'sem número')

  // SÓ AS LOJAS NO AUTOMÁTICO (14/10/2026): com os modos no /resumo, o topo diz quais.
  const canaisAuto = [
    { id: 'k-sh', integration_id: 'i-sh', plataforma: 'shopee', canal: 'chat', modo: 'auto' },
    { id: 'k-ml', integration_id: 'i-ml', plataforma: 'ml', canal: 'pos_venda', modo: 'observar' },
  ]
  const resumoAuto = { ...RESUMO_LISTA, canais: canaisAuto, flags: { humano_ativa: true, humano_modos: ['auto'] } }
  html = await renderizarLista({ itens, resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /data-dica-humano[^>]*>\s*Só as lojas no Automático: o que a IA não pode responder e falta responder — respondeu, sai daqui\./)
  assert.match(html, /title="Só o que falta responder e a IA não pode responder \([^"]*\), nas lojas no Automático — respondeu, sai daqui"/)
  // "todos" no servidor (null): o texto de sempre.
  html = await renderizarLista({ itens, resumo: { ...resumoAuto, flags: { humano_ativa: true, humano_modos: null } }, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /data-dica-humano[^>]*>\s*Só o que a IA não pode responder e falta responder — respondeu, sai daqui\./)
  // Vazia com loja no Automático: a boa notícia de sempre, sem o link.
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /Nada esperando uma pessoa agora — o que a IA não puder responder aparece aqui\./)
  assert.doesNotMatch(html, /data-abrir-lojas/)
  // Vazia porque NENHUMA loja está no Automático: diz isso e aponta "Lojas e modo".
  const semAuto = { ...resumoAuto, humano: 0, canais: canaisAuto.map((c) => ({ ...c, modo: 'observar' })) }
  html = await renderizarLista({ itens: [], resumo: semAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.match(html, /Nenhuma loja está no Automático — a Caixa Humano só mostra as lojas no Automático\./)
  assert.match(html, /<button[^>]*data-abrir-lojas[^>]*>ver em Lojas e modo<\/button>/)
  // … e manda a página abrir a aba (o mesmo `abrirAba` da conversa).
  assert.match(listaSfc.descriptor.template.content, /data-abrir-lojas @click="emit\('abrirAba', 'lojas'\)"/)
  // Nem com um filtro do menu o motivo some (o "tirar o filtro" fica junto).
  html = await renderizarLista({ itens: [], resumo: semAuto, caixa: 'humano', filtros: filtros({ filtro: 'vencidas' }) })
  assert.match(html, /Nenhuma loja está no Automático/)
  assert.match(html, />ver em Lojas e modo</)
  assert.match(html, />tirar o filtro</)
  // A loja escolhida na barra fora do Automático: diz que é ela.
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'ml', integration_id: 'i-ml' }) })
  assert.match(html, /Esta loja não está no Automático — a Caixa Humano só mostra as lojas no Automático\./)
  assert.match(html, />ver em Lojas e modo</)
  // A PLATAFORMA escolhida (no topo) sem loja no Automático: diz que é ela —
  // não "Nada esperando uma pessoa" (o revisor, 14/10/2026: ML, TikTok,
  // Magalu e Amazon em Observar).
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'ml', plataforma_topo: 'ml' }) })
  assert.match(html, /Nenhuma loja do Mercado Livre está no Automático — a Caixa Humano só mostra as lojas no Automático\./)
  assert.doesNotMatch(html, /Nada esperando uma pessoa/)
  assert.match(html, /<button[^>]*data-abrir-lojas[^>]*>ver em Lojas e modo<\/button>/)
  // … sem loja nenhuma dela (TikTok) e no grupo Redes também.
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'tiktok', plataforma_topo: 'tiktok' }) })
  assert.match(html, /Nenhuma loja do TikTok está no Automático/)
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook' }) })
  assert.match(html, /Nenhuma loja do Instagram ou do Facebook está no Automático/)
  // A plataforma COM loja no Automático, vazia: a boa notícia, sem o link.
  html = await renderizarLista({ itens: [], resumo: resumoAuto, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'shopee', plataforma_topo: 'shopee' }) })
  assert.match(html, /Nada esperando uma pessoa agora/)
  assert.doesNotMatch(html, /data-abrir-lojas/)
  // Com os modos "todos" (null), nada disso.
  html = await renderizarLista({ itens: [], resumo: { ...resumoAuto, flags: { humano_ativa: true, humano_modos: null } }, caixa: 'humano', filtros: filtros({ filtro: 'aguardando', plataforma: 'ml' }) })
  assert.match(html, /Nada esperando uma pessoa agora/)
  assert.doesNotMatch(html, /data-abrir-lojas/)
  // Na Caixa, o chip da loja fora dos modos diz "Precisa de pessoa" no title.
  html = await renderizarLista({ itens: [conversa('c-obs', { humano: HUM(['atendente'], { na_caixa: false }) }), conversa('c-auto', { humano: HUM(['reclamacao'], { na_caixa: true }) })], resumo: resumoAuto, filtros: filtros() })
  assert.match(html, /title="Precisa de pessoa — a IA não responderia esta:\n• Pediu atendente" data-selo-humano/)
  assert.match(html, /title="Caixa Humano — a IA não responde esta:\n• Reclamação aberta" data-selo-humano/)
  // A API antiga (sem os modos): nada disso — e na Caixa, nunca.
  html = await renderizarLista({ itens: [], resumo: { ...semAuto, flags: { humano_ativa: true } }, caixa: 'humano', filtros: filtros({ filtro: 'aguardando' }) })
  assert.doesNotMatch(html, /Nenhuma loja está|data-abrir-lojas/)
  html = await renderizarLista({ itens: [], resumo: semAuto, filtros: filtros() })
  assert.doesNotMatch(html, /Nenhuma loja está|data-abrir-lojas|data-dica-humano/)

  // O menu e a volta do filtro (o código de verdade).
  const setup = listaSfc.descriptor.scriptSetup.content
  assert.match(setup, /mudar\('filtro', filtros\.value\.filtro === value \? padrao\.value : value\)/)
  assert.match(listaSfc.descriptor.template.content, /v-for="\(f, i\) in opcoesDoMenu"/)
  assert.match(listaSfc.descriptor.template.content, /v-if="!emHumano && contagem\[f\.value\] !== undefined/, 'na Caixa Humano o menu não mostra os números da Caixa')
}

// ------------------------------------------------ a barra de lojas e o menu do topo com os números da Caixa Humano
async function renderizar(s, props) {
  const { content } = compileScript(s.descriptor, { id: 'x', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.component('AtendimentoIconePlataforma', { props: ['plataforma'], render() { return Vue.h('i', { 'data-icone': this.plataforma }) } })
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
async function numeros() {
  const bolinhas = (html) => [...html.matchAll(/<span class="min-w-0 flex-1 truncate">([^<]+)<\/span>(?:<!--[^>]*-->)*\s*<span[^>]*>(\d+)<\/span>/g)].map((m) => [m[1], m[2]])
  let html = await renderizar(lojasSfc, { resumo: RESUMO_LISTA, filtros: filtros(), numero: 'humano' })
  assert.match(html, /data-lojas-todas>[\s\S]*?Todas<\/span><span[^>]*>5<\/span>/)
  assert.deepEqual(bolinhas(html).filter(([n]) => n !== 'Todas'), [['atv', '4'], ['poofy', '1']])
  assert.match(html, /title="Todas as lojas — 5 esperando uma pessoa \(a IA não pode responder\)"/)
  assert.match(html, /4 na Caixa Humano \(a IA não pode responder\)/)
  // Na Caixa: o "falta responder".
  html = await renderizar(lojasSfc, { resumo: RESUMO_LISTA, filtros: filtros() })
  assert.deepEqual(bolinhas(html).filter(([n]) => n !== 'Todas'), [['atv', '9'], ['poofy', '2']])
  // Cortada na Shopee, na Caixa Humano: "Todas · Shopee" com o 4.
  html = await renderizar(lojasSfc, { resumo: RESUMO_LISTA, filtros: filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }), numero: 'humano' })
  assert.match(html, /Todas · Shopee<\/span><span[^>]*>4<\/span>/)
  assert.doesNotMatch(html, />poofy</)

  // Só as lojas no Automático (14/10/2026): a loja fora SOME da barra da
  // Caixa Humano (o dono: "mostre também só as que estão ativadas"); a do
  // modo, como sempre.
  const resumoModos = {
    ...RESUMO_LISTA,
    humano: 4,
    lojas: [
      { integration_id: 'i-sh', plataforma: 'shopee', conta: 'atv', aguardando: 9, vencidas: 0, humano: 4 },
      { integration_id: 'i-ml', plataforma: 'ml', conta: 'poofy', aguardando: 2, vencidas: 0, humano: 0 },
    ],
    canais: [
      { id: 'k-sh', integration_id: 'i-sh', plataforma: 'shopee', canal: 'chat', modo: 'auto', status: 'ok' },
      { id: 'k-ml', integration_id: 'i-ml', plataforma: 'ml', canal: 'pos_venda', modo: 'observar', status: 'ok' },
    ],
    flags: { humano_ativa: true, humano_modos: ['auto'] },
  }
  html = await renderizar(lojasSfc, { resumo: resumoModos, filtros: filtros(), numero: 'humano' })
  assert.deepEqual(bolinhas(html).filter(([n]) => n !== 'Todas'), [['atv', '4']], 'a poofy (Observar) fora da barra')
  assert.doesNotMatch(html, />poofy</, 'a loja em Observar não aparece na barra da Caixa Humano')
  assert.match(html, /4 na Caixa Humano \(a IA não pode responder\)/)
  // Na Caixa (o "falta responder"), nada muda.
  html = await renderizar(lojasSfc, { resumo: resumoModos, filtros: filtros() })
  assert.doesNotMatch(html, /Fora da Caixa Humano/)

  // O menu do topo: o total e cada plataforma com o número da Caixa Humano.
  html = await renderizar(filtroSfc, { resumo: RESUMO_LISTA, filtros: filtros(), numero: 'humano' })
  const itens = [...html.matchAll(/data-chip="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  assert.deepEqual(itens, [['todas', 'Todas 5'], ['ml', 'Mercado Livre 1'], ['shopee', 'Shopee 4']])
  assert.match(html, /title="Todas as plataformas: 5 conversa\(s\) esperando uma pessoa \(a IA não pode responder\)"/)
  html = await renderizar(filtroSfc, { resumo: RESUMO_LISTA, filtros: filtros() })
  assert.match(html, /data-chip="todas"[^>]*>[\s\S]*?>11<\/span>/, 'na Caixa, o falta responder')
}

// ------------------------------------------------ a faixa na conversa
{
  const tpl = conversaSfc.descriptor.template.content
  const faixa = (tpl.match(/<div\s+v-if="conversa\.humano"[\s\S]*?<\/div>/) || [])[0] || ''
  assert.ok(faixa, 'a faixa existe')
  assert.match(faixa, /data-faixa-humano/)
  // O nome e a frase pela `faixaHumano` (14/10/2026: "Precisa de pessoa" na
  // loja fora dos modos da Caixa Humano, com o title dizendo por quê).
  assert.match(faixa, /<span class="font-semibold">\{\{ faixaHumano\(conversa\.humano\)\.nome \}\}<\/span> — \{\{ faixaHumano\(conversa\.humano\)\.frase \}\}: \{\{ rotulosHumano\(conversa\.humano\)\.join\(' · '\) \}\}/)
  assert.match(faixa, /:title="conversa\.humano\.na_caixa === false \? tituloForaDaCaixaHumano : undefined"/)
  assert.match(conversaSfc.descriptor.scriptSetup.content, /A loja desta conversa não está \$\{modos \? textoModosHumano\(modos\) : 'nos modos da Caixa Humano'\}: ela não aparece na Caixa Humano \(mude em Lojas e modo\)\./)
  // Logo abaixo do cabeçalho, antes das outras faixas.
  assert.ok(tpl.indexOf('data-faixa-humano') < tpl.indexOf('<!-- faixas: bloqueio, janela, fechada'))
  assert.match(conversaSfc.descriptor.scriptSetup.content, /^ {2}rotulosHumano,$/m)
  assert.match(conversaSfc.descriptor.scriptSetup.content, /^ {2}faixaHumano,$/m)
  // A conversa avisa a página (o `mudou` leva o `humano` do detalhe): é por ele
  // que a linha sai da Caixa Humano.
  assert.match(api('routers/atendimento.py'), /humano=humano_svc\.para_tela\(para_pessoa, ia_pausada=c\.ia_pausada, na_caixa=na_caixa\),/)
}

lista().then(numeros).then(
  () => console.log('ok: atendimento-caixa-humano'),
  (e) => { console.error(e); process.exit(1) },
)
