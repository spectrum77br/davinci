// node tests/atendimento-troca-sugestoes.cjs — SUGESTÕES DE TROCA e a lista
// Ag. cancelamento na caixa /atendimento (item 4, fase 4b, 05/10/2026).
// Aqui, a tela:
//  - o contrato com o backend: os tipos SugestaoTroca/ItemTroca/SugestoesTroca
//    são os `SugestaoTrocaOut`/`ItemTrocaOut`/`SugestoesTrocaOut`
//    (schemas/atendimento_painel.py) e os da lista são os de
//    schemas/atendimento_troca.py, nos dois sentidos; o painel traz
//    `sugestoes_troca`; todo nível e todo `motivo_fora` de
//    services/atendimento/troca_sugestoes.py tem texto na tela; a URL da lista
//    é a rota do router `atendimento_troca`; os filtros da lista cobrem os
//    códigos do classificador;
//  - os ajudantes puros (nível, custo, hora do catálogo, prazo de envio…);
//  - o bloco renderizado (Vue SSR): as sugestões com o "copiar oferta", o
//    texto da oferta para copiar (só o que o backend mandou — a tela não
//    escreve oferta), as de fora esmaecidas, o % do custo só quando vem, o
//    aviso e o rodapé do estoque; nada de v-html; sem a permissão, nem
//    "Trocar" (4c) nem "enviar oferta" (4d) — com ela, os dois (o diálogo
//    da troca e a rota da oferta têm o teste deles: tests/atendimento-troca.cjs);
//  - o encaixe: dentro do cartão do Ag. cancelamento; e a lista (diálogo)
//    aberta pelo botão do cabeçalho da lista de conversas, com "Abrir
//    conversa" só quando o pedido tem conversa, a troca aberta com o
//    Retomar e o diálogo da troca por cima.
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
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, render: compiled.code }
}
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function renderDe(sfcx) {
  const compilado = {}
  new Function('exports', 'require', transpile(sfcx.render))(compilado, require)
  return compilado.render
}
// O setup sem os imports, com o que ele usa por parâmetro.
function setupDe(sfcx, params, retorno) {
  const src = sfcx.descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const nomes = Object.keys(params)
  return new Function(...nomes, transpile(src + `\nreturn { ${retorno.join(', ')} }`))(...nomes.map((n) => params[n]))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const icone = (nome) => ({ name: nome, render: () => Vue.h('i', { 'data-icone': nome }) })

const bloco = sfc('../components/AtendimentoTrocaSugestoes.vue')
const T = exportsDe(bloco.descriptor)
const listaSfc = sfc('../components/AtendimentoAgCancelamentoLista.vue')
const L = exportsDe(listaSfc.descriptor)
const cartao = sfc('../components/AtendimentoAgCancelamento.vue')
const G = exportsDe(cartao.descriptor)
const pedido = sfc('../components/AtendimentoPedido.vue')
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
const listaConversas = sfc('../components/AtendimentoLista.vue')

// ------------------------------------------------ o contrato com o backend
const schemasPainel = api('schemas/atendimento_painel.py')
const schemasTroca = api('schemas/atendimento_troca.py')
const servico = api('services/atendimento/troca_sugestoes.py')
const roteador = api('routers/atendimento_troca.py')
const classificador = api('services/atendimento/ag_cancelamento.py')
const painel = api('services/atendimento/painel.py')

const campos = (classe, fonte) => {
  const m = fonte.match(new RegExp(`^class ${classe}\\(BaseModel\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) |$(?![\\s\\S]))`, 'm'))
  assert.ok(m, classe)
  return [...m[1].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
}
const tipo = (nome, fonte) => {
  const m = fonte.match(new RegExp(`export type ${nome} = \\{\\n([\\s\\S]*?)\\n\\}`))
  assert.ok(m, nome)
  return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
}
const igual = (a, b, msg) => assert.deepEqual(a.slice().sort(), b.slice().sort(), msg)
{
  // As sugestões: tipo da tela = schema (nos dois sentidos).
  igual(tipo('SugestaoTroca', pedido.fonte), campos('SugestaoTrocaOut', schemasPainel), 'SugestaoTroca = SugestaoTrocaOut')
  igual(tipo('ItemTroca', pedido.fonte), campos('ItemTrocaOut', schemasPainel), 'ItemTroca = ItemTrocaOut')
  igual(tipo('SugestoesTroca', pedido.fonte), campos('SugestoesTrocaOut', schemasPainel), 'SugestoesTroca = SugestoesTrocaOut')
  // O painel traz o bloco (opcional dos dois lados).
  assert.ok(campos('PainelOut', schemasPainel).includes('sugestoes_troca'), 'PainelOut.sugestoes_troca')
  assert.match(schemasPainel, /^ {4}sugestoes_troca: SugestoesTrocaOut \| None = None$/m)
  assert.match(pedido.fonte, /^ {2}sugestoes_troca\?: SugestoesTroca \| null$/m)
  // A lista: os tipos = os de schemas/atendimento_troca.py.
  igual(tipo('ItemPedidoAg', listaSfc.fonte), campos('ItemPedidoAgOut', schemasTroca), 'ItemPedidoAg = ItemPedidoAgOut')
  igual(tipo('PedidoAgCancelamento', listaSfc.fonte), campos('PedidoAgCancelamentoOut', schemasTroca), 'PedidoAgCancelamento = PedidoAgCancelamentoOut')
  igual(tipo('ListaAgCancelamento', listaSfc.fonte), campos('ListaAgCancelamentoOut', schemasTroca), 'ListaAgCancelamento = ListaAgCancelamentoOut')
  assert.match(schemasTroca, /^ {4}motivo: AgCancelamentoOut$/m, 'o motivo é o bloco do painel')
  assert.match(schemasTroca, /^ {4}sugestoes_troca: SugestoesTrocaOut \| None = None$/m)

  // Todo campo que o bloco lê existe no schema.
  const lidosS = new Set([...bloco.descriptor.template.content.matchAll(/\bs\.([a-z_]+)/g)].map((m) => m[1]))
  for (const c of lidosS) assert.ok(campos('SugestaoTrocaOut', schemasPainel).includes(c), `a tela lê s.${c}`)
  const lidosI = new Set([...bloco.descriptor.template.content.matchAll(/\bit\.([a-z_]+)/g)].map((m) => m[1]))
  for (const c of lidosI) assert.ok(campos('ItemTrocaOut', schemasPainel).includes(c), `a tela lê it.${c}`)
  for (const c of ['sku', 'nome', 'nivel', 'estoque', 'dif_custo_pct', 'motivo_fora', 'texto_oferta']) assert.ok(lidosS.has(c), `a tela usa s.${c}`)
  for (const c of ['sku_original', 'quantidade', 'nome_original', 'sugestoes', 'fora', 'texto_oferta', 'sem_parecido', 'motivo_sem_sugestao']) assert.ok(lidosI.has(c), `a tela usa it.${c}`)
  const lidosP = new Set([...listaSfc.descriptor.template.content.matchAll(/\bp\.([a-z_]+)/g)].map((m) => m[1]))
  for (const c of lidosP) assert.ok(campos('PedidoAgCancelamentoOut', schemasTroca).includes(c), `a lista lê p.${c}`)

  // Os níveis e os motivos de fora do serviço têm texto na tela.
  const constantes = Object.fromEntries([...servico.matchAll(/^([A-Z_0-9]+) = ("?[a-z_0-9]+"?)(?:\s|$)/gm)].map((m) => [m[1], m[2].replace(/"/g, '')]))
  const niveis = ['NIVEL_LOTE', 'NIVEL_MODELO', 'NIVEL_ESPECIFICACAO'].map((n) => Number(constantes[n]))
  igual(Object.keys(T.ROTULO_NIVEL).map(Number), niveis, 'ROTULO_NIVEL = os níveis do serviço')
  const fora = [...((servico.match(/^MOTIVOS_FORA = \(([^)]*)\)$/m) || [])[1] || '').matchAll(/([A-Z_]+)/g)].map((m) => constantes[m[1]])
  assert.equal(fora.length, 3, 'os 3 motivos de fora')
  // + o do custo mascarado para quem não vê a Margem (decisão (g)).
  assert.equal(constantes.FORA_DA_REGRA, 'fora_da_regra')
  igual(Object.keys(T.MOTIVO_FORA), [...fora, constantes.FORA_DA_REGRA], 'MOTIVO_FORA = MOTIVOS_FORA do serviço + FORA_DA_REGRA')

  // A URL da lista = a rota do router novo (prefixo + caminho).
  const prefixo = (roteador.match(/prefix="([^"]+)"/) || [])[1]
  const caminho = (roteador.match(/@router\.get\("([^"]+)"/) || [])[1]
  assert.equal(L.URL_AG_CANCELAMENTO, `${prefixo}${caminho}`)
  assert.match(roteador, /dependencies=\[Depends\(_so_admin\)\]/, 'a trava da caixa')
  // Os filtros da lista cobrem os códigos do classificador (os mesmos do cartão).
  igual(L.FILTROS_AG.map((f) => f.codigo), Object.keys(G.TOM_AG_CANCELAMENTO), 'FILTROS_AG = os códigos do cartão')
  const titulos = Object.fromEntries([...((painel.match(/^TITULO_AG_CANCELAMENTO = \{([\s\S]*?)\n\}/m) || [])[1] || '').matchAll(/^ {4}([A-Z_]+): "([^"]+)"/gm)].map((m) => [m[1], m[2]]))
  // Um título por código (o `em_analise` mascarado tem o TITULO_EM_ANALISE à parte).
  assert.equal(Object.keys(titulos).length + 1, L.FILTROS_AG.length, 'um título por código')
  assert.match(painel, /^TITULO_EM_ANALISE = "Em análise"$/m)
  assert.equal(L.FILTROS_AG.find((f) => f.codigo === 'em_analise').rotulo, 'Em análise')
  // A oferta é escrita SÓ pelo backend (constantes.TEXTO_OFERTA_*): a tela só mostra.
  assert.doesNotMatch(bloco.fonte, /pelo mesmo valor|cancelamos/i)
  assert.match(api('services/atendimento/constantes.py'), /^TEXTO_OFERTA_TROCA = \(/m)
}

// ------------------------------------------------ os ajudantes
{
  assert.equal(T.rotuloNivel({ nivel: 0, mesmo_produto: true }), 'mesmo produto, outro lote')
  assert.equal(T.rotuloNivel({ nivel: 1, mesmo_produto: true }), 'mesmo produto, outro lote (pede aceite)')
  assert.equal(T.rotuloNivel({ nivel: 1, mesmo_produto: false }), 'mesmo modelo, outra cor')
  assert.equal(T.rotuloNivel({ nivel: 2, mesmo_produto: false }), 'outro modelo, mesma especificação')
  assert.equal(T.rotuloNivel({ nivel: 7, mesmo_produto: false }), 'nível 7')

  assert.equal(T.textoCusto(null), null, 'quem não vê a Margem não recebe o número')
  assert.equal(T.textoCusto(undefined), null)
  assert.equal(T.textoCusto(0), 'mesmo custo')
  assert.equal(T.textoCusto(3.24), 'custo +3,2%')
  assert.equal(T.textoCusto(-7.1), 'custo −7,1%')

  assert.equal(T.horaLeitura(null), '')
  assert.equal(T.horaLeitura('xx'), '')
  assert.equal(T.horaLeitura('2026-10-05T18:07:00Z'), '15:07', 'horário de Brasília')
  assert.equal(T.avisoEstoque('2026-10-05T18:07:00Z'), 'Estoque do DaVinci, lido às 15:07; a troca confere ao vivo no Bling.')
  assert.equal(T.avisoEstoque(null), 'Estoque do DaVinci; a troca confere ao vivo no Bling.')
  assert.equal(T.frase('o item em falta já não está no pedido: dg1.ci'), 'O item em falta já não está no pedido: dg1.ci.')
  assert.equal(T.frase(''), '')

  assert.deepEqual(L.filtrosDaLista(null), [])
  assert.deepEqual(
    L.filtrosDaLista({ margem_trava: 2, sem_estoque: 3, manual: 0, novo_codigo: 1 }).map((f) => [f.codigo, f.n]),
    [['sem_estoque', 3], ['margem_trava', 2], ['novo_codigo', 1]],
    'o que pede ação primeiro; a trava da Margem no fim; código novo depois',
  )
  // Filtro por PLATAFORMA (08/10/2026): só as que têm pedido, na ordem dos
  // chips da Caixa; plataforma desconhecida no fim, com o código cru.
  assert.deepEqual(L.plataformasDaLista(null), [])
  assert.deepEqual(
    L.plataformasDaLista([{ plataforma: 'shopee' }, { plataforma: 'ML' }, { plataforma: null }, { plataforma: 'shopee' }, { plataforma: 'nova' }]).map((c) => [c.valor, c.nome, c.n]),
    [['ml', 'Mercado Livre', 1], ['shopee', 'Shopee', 2], ['nova', 'nova', 1]],
  )
  assert.deepEqual(L.contarPorCodigo([{ motivo: { codigo: 'a' } }, { motivo: { codigo: 'a' } }, { motivo: { codigo: 'b' } }]), { a: 2, b: 1 })
  assert.deepEqual(L.contarPorCodigo(null), {})
  {
    const FP = exportsDe(sfc('../components/AtendimentoFiltroPlataforma.vue').descriptor)
    const caixa = FP.GRUPOS_CAIXA.filter((g) => g.plataformas.length === 1).map((g) => [g.valor, g.nome])
    assert.deepEqual(L.PLATAFORMAS_AG.map((x) => [x.valor, x.nome]), caixa, 'a mesma ordem e os mesmos nomes dos chips da Caixa')
  }
  const agora = Date.parse('2026-10-05T12:00:00Z')
  assert.equal(L.prazoDeEnvio(null, agora), null)
  assert.deepEqual(L.prazoDeEnvio('2026-10-05T11:00:00Z', agora), { texto: 'prazo de envio vencido', urgente: true })
  assert.deepEqual(L.prazoDeEnvio('2026-10-05T12:40:00Z', agora), { texto: 'envio vence em 40 min', urgente: true })
  assert.deepEqual(L.prazoDeEnvio('2026-10-05T17:30:00Z', agora), { texto: 'envio vence em 5 h', urgente: true })
  assert.deepEqual(L.prazoDeEnvio('2026-10-08T15:00:00Z', agora), { texto: 'envio até 08/10', urgente: false })
  assert.equal(L.itensEmLinha([{ sku: 'dg053.sp', quantidade: 2 }, { sku: 'a001.sp', quantidade: 1 }]), 'dg053.sp ×2, a001.sp')
  assert.equal(L.itensEmLinha(null), '')
}

// ------------------------------------------------ o bloco renderizado (Vue SSR)
const OFERTA = 'Olá! O produto que você comprou (A17 - Branco) ficou sem estoque. Podemos enviar no lugar: A17 - Laranja — pelo mesmo valor, sem custo a mais para você. Se preferir, cancelamos o pedido. Podemos seguir com a troca?'
const sug = (sku, extra = {}) => ({
  sku,
  nome: `Nome ${sku}`,
  nivel: 1,
  mesmo_produto: false,
  estoque: 9,
  estoque_em: null,
  produto_id: 1,
  dif_custo_pct: 0,
  motivo_fora: null,
  texto_oferta: null,
  ...extra,
})
const SUGESTOES = {
  itens: [
    {
      sku_original: 'dg057.ci+a001.ci',
      quantidade: 2,
      nome_original: 'A17 - Branco <b>+ Fone</b>',
      sugestoes: [
        sug('dg057.sp+a001.sp', { nivel: 0, mesmo_produto: true, estoque: 14 }),
        sug('dg056.ci+a001.ci', { estoque: 114, texto_oferta: OFERTA }),
        sug('dg200.ci+a001.ci', { nivel: 2, dif_custo_pct: 3.2, texto_oferta: 'outra oferta' }),
      ],
      fora: [
        sug('dg055.ci+a001.ci', { estoque: 0, motivo_fora: 'sem_estoque' }),
        sug('dg201.ci+a001.ci', { nivel: 2, dif_custo_pct: 6.5, motivo_fora: 'custo_acima' }),
      ],
      texto_oferta: OFERTA,
      sem_parecido: false,
      motivo_sem_sugestao: null,
    },
    {
      sku_original: 'uaf001m2.220',
      quantidade: 1,
      nome_original: null,
      sugestoes: [],
      fora: [],
      texto_oferta: null,
      sem_parecido: true,
      motivo_sem_sugestao: 'nenhum produto parecido no catálogo',
    },
  ],
  aviso: 'o item em falta já não está no pedido: b026.10',
  catalogo_lido_em: '2026-10-05T18:07:00Z',
  ve_custo: true,
  falhou: false,
}

const ICONES_BLOCO = ['Check', 'Copy', 'Loader2', 'Send', 'Shuffle']
// O bloco montado com o setup de verdade. `extra` = as props da 4c/4d (sem
// elas, os padrões: sem pedido e sem permissão); `responde` = a API falsa.
const ENVIADA = { enviada: true, mensagem_id: 'm-1', texto: 'ok', status: 'enviada', erro: null, conversa_id: 'conv-1', sku_antigo: 'dg057.ci+a001.ci', sku_novo: 'dg056.ci+a001.ci', nivel: 1 }
function montarBloco(sugestoes, extra = {}, responde = async () => structuredClone(ENVIADA)) {
  const props = Vue.reactive({ numero: null, conversaId: null, podeTrocar: false, podeOfertar: false, trocaAberta: null, ofertaEnvio: null, trocaEnvio: null, ...extra, sugestoes })
  const copiados = []
  const chamadas = []
  const emitidos = []
  const estado = setupDe(bloco, {
    computed: Vue.computed,
    ref: Vue.ref,
    onBeforeUnmount: () => {},
    withDefaults: (p) => p,
    defineProps: () => props,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({ api: async (url, opts) => { chamadas.push({ url, opts }); return responde(url, opts) } }),
    avisoEstoque: T.avisoEstoque,
    escolhaDe: T.escolhaDe,
    chaveDe: T.chaveDe,
    motivoSemOferta: T.motivoSemOferta,
    motivoSemTroca: T.motivoSemTroca,
    corpoDaOferta: T.corpoDaOferta,
    erroDaOferta: T.erroDaOferta,
    marcaDaOferta: T.marcaDaOferta,
    urlOferta: T.urlOferta,
    copiar: async (t) => { copiados.push(t); return true },
    erroDaApi: P.erroDaApi,
    erroEnvioLegivel: P.erroEnvioLegivel,
    ...Object.fromEntries(ICONES_BLOCO.map((n) => [n, icone(n)])),
  }, ['rodape', 'copiado', 'copiarOferta', 'trocaLiberada', 'semTroca', 'trocar', 'ofertaVisivel', 'semOferta', 'temOferta', 'confirmando', 'enviandoOferta', 'enviadas', 'erroOferta', 'pedirConfirmacao', 'enviarOferta'])
  return { props, estado, copiados, chamadas, emitidos }
}
async function htmlDe(m) {
  const app = Vue.createSSRApp({ setup: () => ({ ...T, ...m.props, ...m.estado }), render: renderDe(bloco) })
  for (const n of ICONES_BLOCO) app.component(n, icone(n))
  // Sem os comentários de fragmento do SSR (<!--[-->, <!---->).
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
async function renderBloco(sugestoes, extra = {}, responde) {
  const m = montarBloco(sugestoes, extra, responde)
  return { ...m, html: await htmlDe(m) }
}

async function testarBloco() {
  const { html, estado, copiados } = await renderBloco(SUGESTOES)
  assert.match(html, /data-troca-sugestoes/)
  assert.ok(html.includes('Sugestões de troca'))
  assert.ok(html.includes('o cliente paga o mesmo'))
  // O aviso do item que saiu do pedido.
  assert.ok(html.includes('O item em falta já não está no pedido: b026.10.'))
  // O item: "No lugar de <sku> ×2 · nome" (texto, nunca HTML).
  assert.ok(html.includes('dg057.ci+a001.ci'))
  assert.ok(html.includes(' ×2'))
  assert.ok(html.includes('A17 - Branco &lt;b&gt;+ Fone&lt;/b&gt;'), 'o nome vai como texto')
  // As 3 sugestões, na ordem, com o nível.
  const ordem = ['dg057.sp+a001.sp', 'dg056.ci+a001.ci', 'dg200.ci+a001.ci'].map((s) => html.indexOf(`>${s}<`))
  assert.ok(ordem.every((i) => i > 0) && ordem[0] < ordem[1] && ordem[1] < ordem[2], 'as sugestões na ordem do backend')
  assert.ok(html.includes('mesmo produto, outro lote<'))
  assert.ok(html.includes('mesmo modelo, outra cor'))
  assert.ok(html.includes('outro modelo, mesma especificação'))
  assert.ok(html.includes('114 em estoque'))
  assert.ok(html.includes('custo +3,2%') && html.includes('mesmo custo'))
  // "copiar oferta" só nas que têm texto (o nível 0 não tem).
  assert.equal((html.match(/data-copiar-oferta/g) || []).length, 2)
  // O texto da oferta da 1ª com texto, para copiar (select-all), como o backend mandou.
  assert.match(html, /data-texto-oferta/)
  assert.ok(html.includes('select-all'))
  assert.ok(html.includes(OFERTA.replace('Olá!', 'Olá!')))
  // As de fora, esmaecidas, com o porquê.
  assert.match(html, /<ul class="space-y-0.5 opacity-60" data-troca-fora>/)
  assert.ok(html.includes('sem estoque no DaVinci'))
  assert.ok(html.includes('nosso custo sobe mais que o teto (custo +6,5%)'))
  // Sem parecido: o porquê.
  assert.ok(html.includes('Nenhum parecido: nenhum produto parecido no catálogo.'))
  // O rodapé do estoque do DaVinci, com a hora.
  assert.ok(html.includes('Estoque do DaVinci, lido às 15:07; a troca confere ao vivo no Bling.'))
  // Sem o pedido e a permissão (quem só lê): nada de Trocar nem de enviar oferta. Nunca v-html.
  assert.doesNotMatch(html, />\s*Trocar( lote)?\s*</)
  assert.doesNotMatch(html, /data-trocar|data-enviar-oferta|data-oferta-motivo/)
  assert.doesNotMatch(bloco.fonte, /v-html/)

  // Copiar: o texto da sugestão, e o "copiado" na chave dela.
  await estado.copiarOferta('dg057.ci+a001.ci|dg056.ci+a001.ci', OFERTA)
  assert.deepEqual(copiados, [OFERTA])
  assert.equal(estado.copiado.value, 'dg057.ci+a001.ci|dg056.ci+a001.ci')
  await estado.copiarOferta('x|y', null)
  assert.equal(copiados.length, 1, 'sem texto, nada a copiar')

  // Quem não vê a Margem: sem o % (o backend manda null).
  const semCusto = structuredClone(SUGESTOES)
  for (const s of [...semCusto.itens[0].sugestoes, ...semCusto.itens[0].fora]) s.dif_custo_pct = null
  const r2 = await renderBloco(semCusto)
  assert.doesNotMatch(r2.html, /custo [+−]|mesmo custo/)
  // A montagem quebrou: diz, sem inventar sugestão.
  const r3 = await renderBloco({ itens: [], aviso: null, catalogo_lido_em: null, ve_custo: false, falhou: true })
  assert.ok(r3.html.includes(T.FALHOU))
  assert.ok(r3.html.includes('Estoque do DaVinci; a troca confere ao vivo no Bling.'))
  // Item com parecidos, mas nenhum com estoque.
  const r4 = await renderBloco({ ...SUGESTOES, aviso: null, itens: [{ ...SUGESTOES.itens[0], sugestoes: [], texto_oferta: null }] })
  assert.ok(r4.html.includes(T.SEM_ESTOQUE_AGORA))
  assert.doesNotMatch(r4.html, /data-texto-oferta|data-troca-aviso/)
}

// ------------------------------------------------ com a permissão (fases 4c e 4d)
async function testarBlocoComTroca() {
  const LIBERADA = { disponivel: true, motivo: null, texto_motivo: null }
  const base = { numero: '300101', conversaId: 'conv-1', podeTrocar: true, podeOfertar: true, ofertaEnvio: LIBERADA }
  // Os ajudantes.
  const it0 = SUGESTOES.itens[0]
  assert.equal(T.chaveDe(it0, it0.sugestoes[1]), 'dg057.ci+a001.ci|dg056.ci+a001.ci')
  assert.deepEqual(T.escolhaDe(it0, it0.sugestoes[0]), { sku_antigo: 'dg057.ci+a001.ci', sku_novo: 'dg057.sp+a001.sp', nome: 'Nome dg057.sp+a001.sp', nivel: 0, mesmo_produto: true })
  assert.equal(T.rotuloTrocar({ nivel: 0 }), 'Trocar lote')
  assert.equal(T.rotuloTrocar({ nivel: 1 }), 'Trocar')
  assert.equal(T.rotuloTrocar({ nivel: 2 }), 'Trocar')
  assert.equal(T.motivoSemOferta(LIBERADA), null)
  assert.equal(T.motivoSemOferta(null), T.OFERTA_INDISPONIVEL, 'API antiga: cinza')
  assert.equal(T.motivoSemOferta({ disponivel: false, motivo: 'envio_desligado' }), T.MOTIVO_OFERTA.envio_desligado)
  assert.equal(T.motivoSemOferta({ disponivel: false, motivo: 'x', texto_motivo: 'A loja está em Observar.' }), 'A loja está em Observar', 'a frase do backend primeiro (o ponto é da tela)')
  assert.equal(T.motivoSemOferta({ disponivel: false, motivo: 'codigo_novo' }), T.OFERTA_INDISPONIVEL)
  assert.deepEqual(T.corpoDaOferta('dg057.ci', 'dg056.ci', '', 'Olá'), { sku_novo: 'dg056.ci', sku_antigo: 'dg057.ci', conversa_id: null, texto: 'Olá', ultima_vista_id: null, confirmar: false })
  assert.deepEqual(T.corpoDaOferta('a', 'b', 'c', 'Olá', 'm-9', true), { sku_novo: 'b', sku_antigo: 'a', conversa_id: 'c', texto: 'Olá', ultima_vista_id: 'm-9', confirmar: true })
  // O "Trocar": sem o campo (API antiga) pode (a prévia confere tudo); senão o porquê.
  assert.equal(T.motivoSemTroca(null), null)
  assert.equal(T.motivoSemTroca({ disponivel: true }), null)
  assert.equal(T.motivoSemTroca({ disponivel: false, motivo: 'troca_desligada', texto_motivo: 'A troca de produto está desligada (ATENDIMENTO_TROCA_ATIVA).' }), 'A troca de produto está desligada (ATENDIMENTO_TROCA_ATIVA)')
  assert.equal(T.motivoSemTroca({ disponivel: false, motivo: 'pedido_fora_do_piloto' }), T.MOTIVO_OFERTA.pedido_fora_do_piloto)
  // A conversa mudou (o cliente escreveu depois): a frase do backend e o "mesmo assim".
  const mudou = { status: 409, data: { detail: { code: 'conversa_mudou', detail: 'O cliente escreveu às 10:12 depois do que você viu: confira a conversa antes de mandar a oferta.' } } }
  assert.deepEqual(T.erroDaOferta(mudou, { texto: 'x', motivos: ['y'] }), { texto: mudou.data.detail.detail, motivos: [], confirmar: true })
  assert.deepEqual(T.erroDaOferta({ data: { detail: { code: 'envio_repetido' } } }, { texto: 'x', motivos: [] }), { texto: 'x', motivos: [], confirmar: false })
  assert.equal(T.marcaDaOferta({ enviada: true, status: 'enviada' }), 'enviada')
  assert.equal(T.marcaDaOferta({ enviada: false, status: 'revisar' }), 'revisar', 'pode ter saído')
  assert.equal(T.marcaDaOferta({ enviada: false, status: 'falhou' }), null, 'não saiu')

  // Com tudo liberado: "Trocar" em cada elegível (nunca nas de fora), "Trocar
  // lote" no nível 0, e "enviar oferta" só onde há texto de oferta.
  const r = await renderBloco(SUGESTOES, base)
  assert.equal((r.html.match(/data-trocar /g) || []).length, 3)
  for (const sk of ['dg057.sp+a001.sp', 'dg056.ci+a001.ci', 'dg200.ci+a001.ci']) assert.match(r.html, new RegExp(`data-trocar data-sku-novo="${sk.replace(/[.+]/g, '\\$&')}"`))
  assert.doesNotMatch(r.html, /data-sku-novo="dg055/, 'as de fora não trocam')
  assert.match(r.html, /<\/i> Trocar lote</)
  assert.equal((r.html.match(/<\/i> Trocar</g) || []).length, 2)
  assert.equal((r.html.match(/data-enviar-oferta/g) || []).length, 2)
  for (const bt of r.html.match(/<button[^>]*data-enviar-oferta[^>]*>/g) || []) assert.doesNotMatch(bt, /\sdisabled(?=[\s>])/, 'liberada: o botão anda')
  assert.doesNotMatch(r.html, /data-oferta-motivo/)
  // O "Trocar" leva a escolha ao pai (o diálogo é dele).
  r.estado.trocar(it0, it0.sugestoes[2])
  assert.deepEqual(r.emitidos, [['trocar', { sku_antigo: 'dg057.ci+a001.ci', sku_novo: 'dg200.ci+a001.ci', nome: 'Nome dg200.ci+a001.ci', nivel: 2, mesmo_produto: false }]])

  // Uma troca aberta no pedido: sem "Trocar" (uma por vez); a oferta segue.
  const comAberta = await renderBloco(SUGESTOES, { ...base, trocaAberta: { id: 't-1', estado: 'incerta' } })
  assert.doesNotMatch(comAberta.html, /data-trocar/)
  assert.match(comAberta.html, /data-enviar-oferta/)
  comAberta.estado.trocar(it0, it0.sugestoes[1])
  assert.deepEqual(comAberta.emitidos, [], 'nem pelo código')
  // Sem o nº do pedido: nenhum botão de escrita.
  assert.doesNotMatch((await renderBloco(SUGESTOES, { ...base, numero: null })).html, /data-trocar|data-enviar-oferta/)
  // O bloco com só a oferta (`podeTrocar` falso): enviar oferta sim, Trocar não.
  const soOferta = await renderBloco(SUGESTOES, { ...base, podeTrocar: false })
  assert.doesNotMatch(soOferta.html, /data-trocar/)
  assert.match(soOferta.html, /data-enviar-oferta/)

  // Envio desligado: o botão cinza, com o porquê no title e numa linha.
  const desl = await renderBloco(SUGESTOES, { ...base, ofertaEnvio: { disponivel: false, motivo: 'canal_nao_envia', texto_motivo: 'A loja kfa está em Observar: o Duoke responde.' } })
  const botoes = desl.html.match(/<button[^>]*data-enviar-oferta[^>]*>/g) || []
  assert.equal(botoes.length, 2)
  for (const bt of botoes) {
    assert.match(bt, /\sdisabled(?=[\s>])/, 'cinza')
    assert.ok(bt.includes('title="Não dá para enviar: A loja kfa está em Observar: o Duoke responde."'), 'o porquê no title, com um ponto só')
  }
  assert.ok(desl.html.includes('Enviar a oferta pelo DaVinci: A loja kfa está em Observar: o Duoke responde.'))
  desl.estado.pedirConfirmacao('dg057.ci+a001.ci|dg056.ci+a001.ci')
  assert.equal(desl.estado.confirmando.value, null, 'desligado: nem a confirmação abre')
  await desl.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.deepEqual(desl.chamadas, [], 'desligado: nada vai à API')
  // API antiga (sem `oferta_envio`): cinza também.
  const antiga = await renderBloco(SUGESTOES, { ...base, ofertaEnvio: null })
  assert.ok(antiga.html.includes(`Enviar a oferta pelo DaVinci: ${T.OFERTA_INDISPONIVEL}.`))

  // O envio: 1º clique abre a confirmação com o texto; o 2º manda pela rota
  // da oferta (nunca a de responder), com o texto que a pessoa viu.
  const m = montarBloco(SUGESTOES, base)
  const chave = 'dg057.ci+a001.ci|dg056.ci+a001.ci'
  m.estado.pedirConfirmacao(chave)
  assert.equal(m.estado.confirmando.value, chave)
  let h = await htmlDe(m)
  assert.match(h, /data-confirmar-oferta/)
  assert.ok(h.includes('Mandar este texto ao comprador agora, pelo chat da plataforma?'))
  assert.equal((h.match(/data-confirmar-oferta/g) || []).length, 1, 'só na sugestão clicada')
  await m.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.deepEqual(m.chamadas, [{ url: '/api/atendimento/pedidos/300101/troca/oferta', opts: { method: 'POST', body: { sku_novo: 'dg056.ci+a001.ci', sku_antigo: 'dg057.ci+a001.ci', conversa_id: 'conv-1', texto: OFERTA, ultima_vista_id: null, confirmar: false } } }])
  assert.equal(m.estado.confirmando.value, null)
  assert.equal(m.estado.enviadas.value[chave], 'enviada')
  assert.deepEqual(m.emitidos, [['ofertaEnviada', ENVIADA]])
  h = await htmlDe(m)
  assert.ok(h.includes('oferta enviada'))
  // Mensagem para comprador não se desenvia: a 2ª vez não sai.
  await m.estado.enviarOferta(it0, it0.sugestoes[1])
  m.estado.pedirConfirmacao(chave)
  assert.equal(m.chamadas.length, 1)
  assert.equal(m.estado.confirmando.value, null)

  // A recusa do backend (as travas do enviar.py): a frase na sugestão.
  const falha = montarBloco(SUGESTOES, base, async () => { throw { status: 409, data: { detail: { code: 'canal_nao_envia', detail: 'A loja kfa está em Observar: o Duoke responde.' } } } })
  falha.estado.pedirConfirmacao(chave)
  await falha.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.deepEqual(falha.estado.erroOferta.value, { chave, texto: 'A loja kfa está em Observar: o Duoke responde.', motivos: [], confirmar: false })
  assert.doesNotMatch(await htmlDe(falha), /data-oferta-mesmo-assim/, 'só a conversa que mudou pede o "mesmo assim"')
  assert.equal(falha.estado.enviadas.value[chave], undefined)
  assert.deepEqual(falha.emitidos, [], 'recusa: nada saiu')
  assert.ok((await htmlDe(falha)).includes('data-erro-oferta'))
  // O validador reprovou (422): os motivos.
  const reprovada = montarBloco(SUGESTOES, base, async () => { throw { status: 422, data: { detail: { code: 'texto_invalido', detail: ['tem número de telefone'] } } } })
  await reprovada.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.deepEqual(reprovada.estado.erroOferta.value.motivos, ['tem número de telefone'])
  // A PLATAFORMA falhou (200, `falhou`): o porquê legível, e pode tentar de novo.
  let vez = 0
  const caiu = montarBloco(SUGESTOES, base, async () => (++vez === 1 ? { ...ENVIADA, enviada: false, status: 'falhou', erro: 'shopee token_http_401' } : structuredClone(ENVIADA)))
  await caiu.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.equal(caiu.estado.enviadas.value[chave], undefined)
  assert.equal(caiu.estado.erroOferta.value.texto, `A oferta não saiu: ${P.erroEnvioLegivel('shopee token_http_401')}`)
  assert.match(caiu.estado.erroOferta.value.texto, /reconectada/)
  assert.equal(caiu.emitidos.length, 1, 'a mensagem que falhou ficou na conversa: o pai relê')
  await caiu.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.equal(caiu.chamadas.length, 2, 'não saiu: dá para tentar de novo')
  assert.equal(caiu.estado.enviadas.value[chave], 'enviada')
  // Pode ter saído (200, `revisar`): o botão trava e o aviso manda conferir.
  const duvida = montarBloco(SUGESTOES, base, async () => ({ ...ENVIADA, enviada: false, status: 'revisar' }))
  await duvida.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.equal(duvida.estado.enviadas.value[chave], 'revisar')
  h = await htmlDe(duvida)
  assert.ok(h.includes('oferta a conferir'))
  assert.match(h, /data-oferta-a-conferir/)
  assert.ok(h.includes(T.OFERTA_A_CONFERIR))
  await duvida.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.equal(duvida.chamadas.length, 1, 'pode ter saído: não reenvia')

  // A última fala que o bloco tinha vai como `ultima_vista_id`; a conversa
  // que mudou (o cliente escreveu depois) volta 409 e a pessoa decide
  // "enviar mesmo assim" (`confirmar`).
  let tentativa = 0
  const vista = montarBloco(SUGESTOES, { ...base, ofertaEnvio: { ...LIBERADA, ultima_mensagem_id: 'm-7' } }, async () => {
    if (++tentativa === 1) throw mudou
    return structuredClone(ENVIADA)
  })
  vista.estado.pedirConfirmacao(chave)
  await vista.estado.enviarOferta(it0, it0.sugestoes[1])
  assert.equal(vista.chamadas[0].opts.body.ultima_vista_id, 'm-7')
  assert.equal(vista.chamadas[0].opts.body.confirmar, false)
  assert.deepEqual(vista.estado.erroOferta.value, { chave, texto: mudou.data.detail.detail, motivos: [], confirmar: true })
  assert.equal(vista.estado.enviadas.value[chave], undefined, 'nada saiu')
  h = await htmlDe(vista)
  assert.match(h, /data-oferta-mesmo-assim/)
  assert.ok(h.includes('O cliente escreveu às 10:12'))
  await vista.estado.enviarOferta(it0, it0.sugestoes[1], true)
  assert.equal(vista.chamadas.length, 2)
  assert.equal(vista.chamadas[1].opts.body.confirmar, true, 'o "mesmo assim"')
  assert.equal(vista.estado.enviadas.value[chave], 'enviada')
  assert.match(bloco.descriptor.template.content, /data-oferta-mesmo-assim\n\s+@click="enviarOferta\(it, s, true\)"/)

  // O "Trocar" com a troca desligada / fora do piloto / uma trava do pedido:
  // cinza, com o porquê no title e numa linha — e nem pelo código.
  const semTrocar = await renderBloco(SUGESTOES, { ...base, trocaEnvio: { disponivel: false, motivo: 'pedido_fora_do_piloto', texto_motivo: 'Este pedido não está na lista piloto da troca de produto.' } })
  const trocas = semTrocar.html.match(/<button[^>]*data-trocar [^>]*>/g) || []
  assert.equal(trocas.length, 3)
  for (const bt of trocas) {
    assert.match(bt, /\sdisabled(?=[\s>])/, 'cinza')
    assert.ok(bt.includes('title="Não dá para trocar: Este pedido não está na lista piloto da troca de produto."'))
  }
  assert.ok(semTrocar.html.includes('Trocar pelo DaVinci: Este pedido não está na lista piloto da troca de produto.'))
  semTrocar.estado.trocar(it0, it0.sugestoes[1])
  assert.deepEqual(semTrocar.emitidos, [], 'cinza: nem pelo código')
  // Liberado (o padrão de `base`, sem o campo): anda, sem a linha do porquê.
  assert.doesNotMatch(r.html, /data-troca-motivo/)
  for (const bt of r.html.match(/<button[^>]*data-trocar [^>]*>/g) || []) assert.doesNotMatch(bt, /\sdisabled(?=[\s>])/)

  // A rota da oferta = a do backend.
  const roteadorTroca = api('routers/atendimento_troca.py')
  assert.match(roteadorTroca, /"\/pedidos\/\{numero_bling\}\/troca\/oferta"/, 'a rota da oferta no router')
  const prefixo = (roteadorTroca.match(/prefix="([^"]+)"/) || [])[1]
  assert.equal(T.urlOferta('300101'), `${prefixo}/pedidos/300101/troca/oferta`)
  // O bloco só fala com a rota da oferta (nunca a de responder/enviar da conversa).
  assert.doesNotMatch(bloco.descriptor.scriptSetup.content, /\/responder|\/enviar|\/conversas\//)
}

// ------------------------------------------------ o encaixe no cartão
{
  const tpl = cartao.descriptor.template.content
  const encaixe = (tpl.match(/<AtendimentoTrocaSugestoes\n[\s\S]*?\/>/) || [])[0]
  assert.ok(encaixe, 'as sugestões no cartão')
  // O pedido, a conversa, quem pode (`useAcessoDaTroca`), a troca aberta e a oferta (4c/4d).
  for (const a of ['v-if="sugestoes"', ':sugestoes="sugestoes"', ':numero="numero"', ':conversa-id="conversaId"', ':pode-trocar="acesso.trocar"', ':pode-ofertar="acesso.ofertar"', ':troca-aberta="trocaAberta"', ':oferta-envio="ag.oferta_envio ?? null"', ':troca-envio="ag.troca_envio ?? null"', '@trocar=', '@oferta-enviada="emit(\'mudou\')"']) {
    assert.ok(encaixe.includes(a), `o cartão passa ${a}`)
  }
  // Dentro do cartão (o div com as cores do motivo), depois da frase.
  const pos = tpl.indexOf('<AtendimentoTrocaSugestoes')
  assert.ok(pos > tpl.indexOf(':class="cls.cartao"'), 'dentro do cartão')
  assert.match(cartao.descriptor.scriptSetup.content, /sugestoes\?: SugestoesTroca \| null/)
  assert.match(cartao.descriptor.scriptSetup.content, /const acesso = useAcessoDaTroca\(\)/)
  assert.match(pedido.descriptor.template.content, /:sugestoes="sugestoesTroca"/)
  assert.match(pedido.descriptor.scriptSetup.content, /const sugestoesTroca = computed\(\(\) => pnl\.value\?\.sugestoes_troca \?\? null\)/)
}

// ------------------------------------------------ a lista Ag. cancelamento
{
  // O botão no cabeçalho da lista de conversas abre o diálogo; "Abrir
  // conversa" de lá seleciona a conversa.
  const tpl = listaConversas.descriptor.template.content
  const setup = listaConversas.descriptor.scriptSetup.content
  assert.match(tpl, /data-abrir-ag-cancelamento\n\s+@click="agListaAberta = true"/)
  assert.ok(tpl.indexOf('data-abrir-ag-cancelamento') < tpl.indexOf('<!-- itens -->'), 'no cabeçalho')
  assert.match(tpl, /<AtendimentoAgCancelamentoLista v-if="agListaAberta" v-model:aberto="agListaAberta" @abrir-conversa="abrirDaListaAg" \/>/)
  const ini = setup.indexOf('const agListaAberta')
  const js = transpile(setup.slice(ini, setup.indexOf('\n}\n', ini) + 3)) + '\nreturn { agListaAberta, abrirDaListaAg }'
  const emitidos = []
  const x = new Function('ref', 'emit', js)(Vue.ref, (...a) => emitidos.push(a))
  x.agListaAberta.value = true
  x.abrirDaListaAg('c-1')
  assert.equal(x.agListaAberta.value, false)
  assert.deepEqual(emitidos, [['selecionar', 'c-1']])
}

const LISTA = {
  pedidos: [
    {
      numero: '300101',
      numeroloja: 'SHP1',
      bling_id: 1,
      loja: '7001',
      plataforma: 'shopee',
      conta: 'kfa',
      data: '2026-10-04T12:00:00Z',
      prazo_envio: '2026-10-05T17:00:00Z',
      motivo: { codigo: 'sem_estoque', titulo: 'Falta de estoque', texto: 'falta de estoque: dg053.sp', etiqueta: true, fala_cancelamento: true, pode_sugerir_troca: true, skus: ['dg053.sp'], conflito: null, observacao_topo: null, troca_aberta: null },
      itens: [{ sku: 'dg053.sp', descricao: 'A17', quantidade: 1 }],
      conversa_id: 'conv-1',
      sugestoes_troca: { itens: [], aviso: null, catalogo_lido_em: null, ve_custo: true, falhou: false },
    },
    {
      numero: '300102',
      numeroloja: null,
      bling_id: 2,
      loja: null,
      plataforma: null,
      conta: null,
      data: null,
      prazo_envio: null,
      motivo: { codigo: 'margem_trava', titulo: 'Trava interna da Margem', texto: 'trava interna', etiqueta: false, fala_cancelamento: false, pode_sugerir_troca: false, skus: [], conflito: null, observacao_topo: null, troca_aberta: null },
      itens: [],
      conversa_id: null,
      sugestoes_troca: null,
    },
  ],
  total: 2,
  por_codigo: { sem_estoque: 1, margem_trava: 1 },
  desde: '2026-08-06T12:00:00Z',
  sugestoes_ativas: true,
  ve_custo: true,
  gerado_em: '2026-10-05T12:00:00Z',
}

const trocaSfc = sfc('../components/AtendimentoTroca.vue')
const TR = exportsDe(trocaSfc.descriptor)

// As trocas abertas que o GET /trocas?abertas=true devolve (a 300101 está na
// lista de 83955; a 300777 parou no meio com o pedido já em 9).
const TROCAS_ABERTAS = {
  itens: [
    { id: 't-9', pedido_bling: '300101', conversa_id: 'conv-1', estado: 'incerta', aberta: true, sku_antigo: 'dg053.sp', sku_novo: 'dg056.sp', nivel: 1, automatica: false, criado_por_nome: 'Heisenberg', created_at: '2026-10-08T12:00:00Z', codigo_erro: null, erro: null, pode_retomar: true },
    { id: 't-7', pedido_bling: '300777', conversa_id: null, estado: 'em_atendido', aberta: true, sku_antigo: 'dg053.ci', sku_novo: 'dg053.sp', nivel: 0, automatica: true, criado_por_nome: 'Robô de lote (troca automática)', created_at: '2026-10-08T11:00:00Z', codigo_erro: 'bling_indisponivel', erro: 'O Bling não mudou a situação agora: use Retomar.', pode_retomar: true },
  ],
}

function montarLista(trocar = false, trocasAbertas = { itens: [] }) {
  const urls = []
  const emitidos = []
  const aberto = Vue.ref(true)
  const acesso = Vue.computed(() => ({ trocar, ofertar: trocar }))
  const estado = setupDe(listaSfc, {
    computed: Vue.computed,
    ref: Vue.ref,
    watch: Vue.watch,
    nextTick: Vue.nextTick,
    defineModel: () => aberto,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({ api: async (url, opts) => {
      urls.push(opts?.query ? `${url}?abertas=${opts.query.abertas}` : url)
      return structuredClone(url === TR.URL_TROCAS ? trocasAbertas : LISTA)
    } }),
    useAcessoDaTroca: () => acesso,
    URL_TROCAS: TR.URL_TROCAS,
    trocasForaDaLista: L.trocasForaDaLista,
    CLS_AG_CANCELAMENTO: G.CLS_AG_CANCELAMENTO,
    leituraAgCancelamento: G.leituraAgCancelamento,
    erroDaApi: (e, p) => ({ texto: p }),
    URL_AG_CANCELAMENTO: L.URL_AG_CANCELAMENTO,
    filtrosDaLista: L.filtrosDaLista,
    plataformasDaLista: L.plataformasDaLista,
    contarPorCodigo: L.contarPorCodigo,
    onClickOutside: () => {},
    prazoDeEnvio: L.prazoDeEnvio,
  }, ['aberto', 'lista', 'trocas', 'carregando', 'erro', 'filtro', 'plataforma', 'plataformas', 'plataformaAtual', 'menuPlataforma', 'escolherPlataforma', 'daPlataforma', 'agora', 'filtros', 'trocasParadas', 'pedidos', 'caixa', 'carregar', 'fechar', 'abrir', 'acesso', 'trocaVisivel', 'troca', 'abrirTroca', 'retomarParada'])
  return { urls, emitidos, aberto, estado }
}
const ICONES_LISTA = ['Check', 'ChevronDown', 'Loader2', 'MessagesSquare', 'PackageX', 'RotateCcw', 'Shuffle', 'X']
async function htmlDaLista(estado) {
  const icones = Object.fromEntries(ICONES_LISTA.map((n) => [n, icone(n)]))
  const app = Vue.createSSRApp({ setup: () => ({ ...L, ...estado, ...icones, rotuloEstado: TR.rotuloEstado, quemTroca: TR.quemTroca }), render: renderDe(listaSfc) })
  for (const [n, c] of Object.entries(icones)) app.component(n, c)
  app.component('AtendimentoPlataforma', { props: ['codigo'], render() { return Vue.h('span', { 'data-plataforma': this.codigo }) } })
  app.component('AtendimentoIconePlataforma', { props: ['plataforma', 'tamanho', 'decorativo'], render() { return Vue.h('i', { 'data-icone-plataforma': this.plataforma }) } })
  app.component('AtendimentoTrocaSugestoes', {
    props: ['sugestoes', 'numero', 'conversaId', 'podeTrocar', 'podeOfertar', 'trocaAberta', 'ofertaEnvio', 'trocaEnvio'],
    render() {
      return Vue.h('div', {
        'data-stub-troca': '1',
        'data-stub-numero': this.numero,
        'data-stub-conversa': this.conversaId,
        'data-stub-pode-trocar': String(this.podeTrocar),
        'data-stub-oferta': this.ofertaEnvio ? String(this.ofertaEnvio.disponivel) : 'sem',
        'data-stub-troca-envio': this.trocaEnvio ? String(this.trocaEnvio.disponivel) : 'sem',
      })
    },
  })
  app.component('AtendimentoTroca', {
    props: ['numero', 'escolha', 'conversaId', 'trocaAberta', 'aberto'],
    render() { return Vue.h('div', { 'data-stub-dialogo-troca': this.numero, 'data-stub-escolha': this.escolha?.sku_novo || '', 'data-stub-aberta': this.trocaAberta?.id || '' }) },
  })
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

async function testarLista() {
  const { urls, emitidos, aberto, estado } = montarLista(false)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(urls, ['/api/atendimento/ag-cancelamento', '/api/atendimento/trocas?abertas=true'], 'abriu: leu a lista (e as trocas abertas) uma vez')
  estado.agora.value = Date.parse('2026-10-05T12:00:00Z')
  const html = await htmlDaLista(estado)
  assert.match(html, /data-lista-ag-cancelamento/)
  assert.match(html, /role="dialog"/)
  assert.ok(html.includes('Aguardando Cancelamento no Bling'))
  assert.ok(html.includes('com ou sem conversa'))
  // Os filtros com os contadores do backend.
  assert.ok(html.includes('Todos (2)') && html.includes('Falta de estoque (1)') && html.includes('Trava da Margem (1)'))
  // O pedido sem estoque: o motivo da mesma leitura do cartão, os itens, o
  // prazo, as sugestões e "Abrir conversa".
  const p1 = html.slice(html.indexOf('data-numero="300101"'), html.indexOf('data-numero="300102"'))
  assert.ok(p1.includes('Falta de estoque: dg053.sp.'))
  assert.ok(p1.includes('envio vence em 5 h'))
  assert.ok(p1.includes('dg053.sp'))
  assert.match(p1, /data-plataforma="shopee"/)
  assert.match(p1, /data-stub-troca/)
  assert.match(p1, /data-abrir-conversa/)
  // A trava da Margem: sem conversa, sem sugestões, sem falar em cancelamento.
  const p2 = html.slice(html.indexOf('data-numero="300102"'))
  assert.ok(p2.includes('sem conversa no DaVinci'))
  assert.doesNotMatch(p2, /data-stub-troca|data-abrir-conversa|Pode falar/)
  assert.ok(p2.includes(G.TEXTO_TRAVA_MARGEM))
  // Quem só lê: "Só leitura", as sugestões sem a permissão, nada de Trocar nem
  // de diálogo da troca. Nunca v-html.
  assert.ok(html.includes('Só leitura: nada muda no Bling por aqui.'))
  assert.match(p1, /data-stub-pode-trocar="false"/)
  assert.match(p1, /data-stub-numero="300101" data-stub-conversa="conv-1"/, 'as sugestões recebem o pedido e a conversa')
  assert.match(p1, /data-stub-oferta="sem"/, 'sem `oferta_envio` (API antiga): o bloco recebe null')
  assert.doesNotMatch(html, />\s*Trocar\s*<|data-stub-dialogo-troca|data-retomar-troca/)
  assert.doesNotMatch(listaSfc.fonte, /v-html/)

  // O filtro pelo motivo é local (os contadores são os de antes do filtro).
  estado.filtro.value = 'margem_trava'
  assert.deepEqual(estado.pedidos.value.map((x) => x.p.numero), ['300102'])
  // "Abrir conversa": emite o id e fecha; sem conversa, nada.
  estado.abrir(LISTA.pedidos[1])
  assert.deepEqual(emitidos, [])
  estado.abrir(LISTA.pedidos[0])
  assert.deepEqual(emitidos, [['abrirConversa', 'conv-1']])
  assert.equal(aberto.value, false)
  // Quem só lê não abre a troca nem pelo código.
  estado.abrirTroca(LISTA.pedidos[0], { sku_antigo: 'dg053.sp', sku_novo: 'dg053.ci', nome: null, nivel: 0, mesmo_produto: true })
  assert.equal(estado.trocaVisivel.value, false)

  // FILTRO POR PLATAFORMA: com uma plataforma só na lista, a fileira não aparece.
  estado.filtro.value = ''
  aberto.value = true
  assert.doesNotMatch(await htmlDaLista(estado), /filtrar pela plataforma/)
  // Com duas, aparece o MENU (igual ao da Caixa): um botão "Todas 2 ▾" e, ao
  // abrir, Todas e cada plataforma com a logo e quantos tem, na ordem da Caixa.
  estado.lista.value.pedidos[1].plataforma = 'ml'
  let comFiltro = await htmlDaLista(estado)
  assert.match(comFiltro, /aria-haspopup="menu" aria-expanded="false" aria-label="filtrar pela plataforma"/)
  assert.match(comFiltro, /data-plataforma-botao[^>]*>[\s\S]*?Todas[\s\S]*?>2<[\s\S]*?<\/button>/)
  assert.match(comFiltro, /role="menu" aria-label="escolher a plataforma"[^>]*style="display:none;"/, 'o menu começa fechado')
  const opcoes = [...comFiltro.matchAll(/data-filtro-plataforma="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  assert.deepEqual(opcoes, [['todas', 'Todas 2'], ['ml', 'Mercado Livre 1'], ['shopee', 'Shopee 1']], 'Mercado Livre antes da Shopee')
  assert.match(comFiltro, /data-filtro-plataforma="ml"[^>]*>[\s\S]*?data-icone-plataforma="ml"/, 'cada opção com a logo')
  // Escolher a Shopee pelo menu: fecha, só os pedidos dela, e os motivos contam só ela.
  estado.menuPlataforma.value = true
  estado.escolherPlataforma('shopee')
  assert.equal(estado.menuPlataforma.value, false)
  assert.equal(estado.plataformaAtual.value.valor, 'shopee')
  await Vue.nextTick()
  assert.deepEqual(estado.pedidos.value.map((x) => x.p.numero), ['300101'])
  assert.deepEqual(estado.filtros.value.map((f) => [f.codigo, f.n]), [['sem_estoque', 1]])
  comFiltro = await htmlDaLista(estado)
  assert.ok(comFiltro.includes('Todos (1)') && comFiltro.includes('Falta de estoque (1)'))
  assert.doesNotMatch(comFiltro, /Trava da Margem \(/)
  // Motivo escolhido que não existe na plataforma nova: volta para "Todos".
  estado.plataforma.value = ''
  await Vue.nextTick()
  estado.filtro.value = 'margem_trava'
  estado.plataforma.value = 'shopee'
  await Vue.nextTick()
  assert.equal(estado.filtro.value, '')
  // Plataforma sem pedido com o motivo: a frase diz os dois filtros.
  estado.plataforma.value = 'ml'
  await Vue.nextTick()
  estado.filtro.value = 'sem_estoque'
  assert.ok((await htmlDaLista(estado)).includes('Nenhum pedido em Aguardando Cancelamento nesta plataforma com este motivo.'))
}

// A lista para quem MEXE (fase 4c): o aviso muda, as sugestões têm o Trocar, a
// troca aberta tem o Retomar, e o diálogo da troca abre por cima da lista.
async function testarListaComTroca() {
  const { estado, urls, aberto } = montarLista(true)
  await new Promise((r) => setTimeout(r, 0))
  const aberta = {
    id: 't-9', estado: 'incerta', sku_antigo: 'dg053.sp', sku_novo: 'dg056.sp', nivel: 1, automatica: false,
    criado_por_nome: 'Heisenberg', created_at: '2026-10-08T12:00:00Z', codigo_erro: 'bling_indisponivel', erro: 'O Bling não respondeu ao PUT.', pode_retomar: true,
  }
  estado.lista.value.pedidos[0].motivo.troca_aberta = aberta
  estado.lista.value.pedidos[0].motivo.oferta_envio = { disponivel: false, motivo: 'envio_desligado', texto_motivo: null }
  let html = await htmlDaLista(estado)
  assert.ok(html.includes('O &quot;Trocar&quot; confere ao vivo no Bling'))
  assert.doesNotMatch(html, /Só leitura: nada muda/)
  const p1 = html.slice(html.indexOf('data-numero="300101"'), html.indexOf('data-numero="300102"'))
  assert.match(p1, /data-stub-pode-trocar="true"/)
  assert.match(p1, /data-stub-oferta="false"/)
  assert.match(p1, /data-troca-aberta data-estado="incerta"/)
  assert.ok(p1.includes(TR.rotuloEstado('incerta')))
  assert.ok(p1.includes('Heisenberg, 08/10 09:00'))
  assert.match(p1, /data-retomar-troca/)
  assert.doesNotMatch(html, /data-stub-dialogo-troca/, 'o diálogo só com o clique')
  // "Trocar" numa sugestão: o diálogo com a escolha e o pedido.
  const escolha = { sku_antigo: 'dg053.sp', sku_novo: 'dg056.sp', nome: null, nivel: 1, mesmo_produto: false }
  estado.abrirTroca(estado.lista.value.pedidos[0], escolha)
  assert.equal(estado.trocaVisivel.value, true)
  html = await htmlDaLista(estado)
  assert.match(html, /data-stub-dialogo-troca="300101" data-stub-escolha="dg056.sp" data-stub-aberta>/)
  // Com o diálogo da troca por cima, o Esc/clique fora não fecham a lista.
  estado.fechar()
  assert.equal(aberto.value, true, 'a lista fica')
  estado.trocaVisivel.value = false
  // "Retomar": o diálogo na troca aberta, sem escolha.
  estado.abrirTroca(estado.lista.value.pedidos[0], null)
  html = await htmlDaLista(estado)
  assert.match(html, /data-stub-dialogo-troca="300101" data-stub-escolha data-stub-aberta="t-9"/)
  // Sem troca aberta, o Retomar não abre nada.
  estado.trocaVisivel.value = false
  estado.abrirTroca(estado.lista.value.pedidos[1], null)
  assert.equal(estado.trocaVisivel.value, false)
  // O diálogo fechou depois de uma escrita, ou a oferta saiu: a lista se relê.
  const tpl = listaSfc.descriptor.template.content
  assert.match(tpl, /<AtendimentoTroca\n[\s\S]*?@mudou="carregar"[\s\S]*?\/>/)
  assert.match(tpl, /@oferta-enviada="carregar"/)
  assert.ok(tpl.indexOf('<AtendimentoTroca\n') > tpl.lastIndexOf('</article>'), 'o diálogo fora dos pedidos (a lista relida não o derruba)')
  await estado.carregar()
  assert.equal(urls.length, 4)
}

// TROCAS PARADAS NO MEIO (fase 4c): a troca aberta cujo pedido saiu de 83955
// (o PATCH 6 falhou e o espelho foi a 9) não some — seção própria com o Retomar.
async function testarTrocasParadas() {
  // O ajudante: só as abertas de pedido FORA da lista, a mais velha primeiro.
  const fora = L.trocasForaDaLista(TROCAS_ABERTAS.itens, ['300101', '300102'])
  assert.deepEqual(fora.map((t) => t.id), ['t-7'])
  assert.deepEqual(L.trocasForaDaLista(null, []), [])
  assert.deepEqual(L.trocasForaDaLista([{ ...TROCAS_ABERTAS.itens[1], aberta: false }], []), [], 'fechada não entra')
  const ordem = L.trocasForaDaLista([{ ...TROCAS_ABERTAS.itens[1], id: 'b', created_at: '2026-10-08T12:00:00Z' }, { ...TROCAS_ABERTAS.itens[1], id: 'a', created_at: '2026-10-07T12:00:00Z' }], [])
  assert.deepEqual(ordem.map((t) => t.id), ['a', 'b'])

  // Quem só lê: vê a parada e o estado, sem o Retomar.
  const so = montarLista(false, TROCAS_ABERTAS)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(so.estado.trocasParadas.value.map((t) => t.id), ['t-7'])
  let html = await htmlDaLista(so.estado)
  assert.match(html, /data-trocas-paradas/)
  assert.ok(html.includes('Trocas paradas no meio (1)'))
  const parada = html.slice(html.indexOf('data-troca-parada'), html.indexOf('</section>', html.indexOf('data-troca-parada')))
  assert.match(parada, /data-numero="300777" data-estado="em_atendido"/)
  assert.ok(parada.includes(TR.rotuloEstado('em_atendido')))
  assert.ok(parada.includes('O Bling não mudou a situação agora: use Retomar.'))
  assert.doesNotMatch(parada, /data-retomar-troca/)
  so.estado.retomarParada(TROCAS_ABERTAS.itens[1])
  assert.equal(so.estado.trocaVisivel.value, false, 'quem só lê não retoma nem pelo código')
  // A da 300101 (ainda em 83955) fica no cartão do pedido, não na seção.
  assert.equal((html.match(/data-troca-parada /g) || []).length, 1)

  // Quem mexe: o Retomar abre o diálogo na troca parada (pedido e conversa dela).
  const mexe = montarLista(true, TROCAS_ABERTAS)
  await new Promise((r) => setTimeout(r, 0))
  html = await htmlDaLista(mexe.estado)
  assert.match(html.slice(html.indexOf('data-trocas-paradas'), html.indexOf('data-pedido-ag')), /data-retomar-troca/)
  mexe.estado.retomarParada(mexe.estado.trocasParadas.value[0])
  assert.equal(mexe.estado.trocaVisivel.value, true)
  html = await htmlDaLista(mexe.estado)
  assert.match(html, /data-stub-dialogo-troca="300777" data-stub-escolha data-stub-aberta="t-7"/)
  // Alguém conduzindo agora: sem o Retomar.
  mexe.estado.trocaVisivel.value = false
  mexe.estado.trocas.value = [{ ...TROCAS_ABERTAS.itens[1], pode_retomar: false }]
  html = await htmlDaLista(mexe.estado)
  assert.ok(html.includes('alguém está conduzindo agora'))
  mexe.estado.retomarParada(mexe.estado.trocas.value[0])
  assert.equal(mexe.estado.trocaVisivel.value, false)
  // Sem trocas abertas (ou o GET delas falhou): nada da seção.
  mexe.estado.trocas.value = []
  assert.doesNotMatch(await htmlDaLista(mexe.estado), /data-trocas-paradas/)
}

testarBloco()
  .then(testarBlocoComTroca)
  .then(testarLista)
  .then(testarListaComTroca)
  .then(testarTrocasParadas)
  .then(() => console.log('ok: atendimento-troca-sugestoes'), (e) => {
    console.error(e)
    process.exit(1)
  })
