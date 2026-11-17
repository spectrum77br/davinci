// node tests/atendimento-reembolso-cliente.cjs — o REEMBOLSO AO CLIENTE pela conversa
// (Novos procedimentos 3/6, 17/11/2026). O dono aprovou: o reembolso do tipo Cliente
// sai da aba Pedido do painel da conversa, logo abaixo de "Valor pago pelo comprador"
// (e no ramo sem retrato), com aprovação do ADM acima do limite e o Pix protegido.
//  - os ajudantes puros: o valor em R$, o limite do ADM (> R$ 100, > 25% do pago, o
//    valor inteiro), o formulário, o corpo do POST, os erros, o aviso dos anteriores
//    e o atalho da triagem;
//  - o bloco (AtendimentoReembolsoCliente): a lista com a situação em chip, o Pix só
//    no que o servidor manda (ADM), o Aprovar do ADM, o "Lançar" desligado com o porquê;
//  - o diálogo (AtendimentoReembolsoLancar): os campos, os avisos, o corpo, o toast,
//    as recusas e a resposta que não chegou; o Pix nunca vai para toast nem evento;
//    17/11/2026 (a revisão): relê o GET ao abrir, manda o nº do Bling que mostrou
//    (409 pedido_mudou), trava o "Lançar" até fechar quando a resposta não chegou,
//    o fundo escuro só fecha com o clique inteiro (e pergunta) e o Esc vale sempre;
//  - o painel (AtendimentoPedido): os dois encaixes, o diálogo fora dos ramos, o
//    atalho que rola até o bloco e o pedido que muda com o diálogo aberto;
//  - a conversa (AtendimentoConversa): o atalho na faixa da Caixa Humano.
// Aqui: Vue SSR com a API falsa (o molde de atendimento-troca e atendimento-devolucao-pedido).
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
  return { descriptor, fonte, filename, render: compiled.code }
}
// O `useAcessoDaTroca` (AtendimentoTroca.vue, no painel) lê as auto-importações do Nuxt.
Object.assign(globalThis, {
  computed: Vue.computed,
  ref: Vue.ref,
  useAuthStore: () => ({ user: {} }),
  useCan: () => Vue.ref(false),
})
const cache = new Map()
const icone = (nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) })
const icones = new Proxy({}, { get: (_, nome) => icone(nome) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
function exportsDe(descriptor) {
  const mod = { exports: {} }
  if (!descriptor.script) return mod.exports
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
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
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const semComentarios = (h) => h.replace(/<!--[\s\S]*?-->/g, '')
// O espaço do Intl ("R$ 50,00") vira espaço comum; as marcas somem; as entidades voltam.
const texto = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
  .replace(/[\s ]+/g, ' ').trim()
const esp = (s) => String(s).replace(/ /g, ' ')
const esperar = () => new Promise((r) => setTimeout(r, 0))
const botao = (h, dado) => (h.match(new RegExp(`<button[^>]*${dado}[^>]*>`)) || [])[0] || ''
const desligado = (tag) => /\sdisabled(?=[\s>=])/.test(tag)

const P = requerer('~/components/AtendimentoPlataforma.vue')
const blocoSfc = sfc('../components/AtendimentoReembolsoCliente.vue')
const RC = exportsDe(blocoSfc.descriptor)
const dialogoSfc = sfc('../components/AtendimentoReembolsoLancar.vue')

// A API falsa: guarda as chamadas e devolve (ou lança) o que `responde` mandar.
function apiFalsa(responde) {
  const chamadas = []
  return {
    chamadas,
    api: async (url, opts) => {
      chamadas.push({ url, metodo: (opts && opts.method) || 'GET', body: opts && opts.body !== undefined ? structuredClone(opts.body) : undefined })
      const r = await responde(url, opts, chamadas.length)
      if (r instanceof Error) throw r
      return structuredClone(r)
    },
  }
}
function toastsFalsos() {
  const lista = []
  const empurra = (tipo) => (t, l) => lista.push([tipo, t, l])
  return { lista, success: empurra('ok'), error: empurra('erro'), info: empurra('info'), warning: empurra('aviso') }
}
const erroHttp = (status, detail) => Object.assign(new Error(String(status)), { status, statusCode: status, data: { detail } })
const semRede = () => new Error('[POST] "/api/atendimento/…": <no response> Failed to fetch')

const PEDIDO = { bling: '300101', marketplace: '251117ABC', plataforma: 'shopee', conta: 'Shopee ATV', valor_pago: 189.9 }
const item = (extra = {}) => ({
  id: 'r1', tipo: 'Cliente', valor: 50, motivo: 'defeito_avaria', motivo_detalhe: 'veio riscado', forma: 'pix', situacao: 'em_aberto',
  criado_por: 'israel', criado_em: '2026-11-17T13:30:00Z', aprovado_por: null, aprovado_em: null, dados_pagamento: null, ...extra,
})
const resposta = (extra = {}) => ({
  pedido: PEDIDO, plataforma_reembolsou: false, limite: { valor: 100, pct: 25 }, pode_lancar: true, motivo_bloqueio: null, pode_aprovar: false, itens: [], ...extra,
})
const PIX = 'chave 11 99999-0000 · Banco Exemplo · Fulana de Tal'

async function principal() {
  // ------------------------------------------------ as URLs e as listas do dono
  {
    assert.equal(RC.urlReembolsos('c1'), '/api/atendimento/conversas/c1/reembolsos')
    assert.equal(RC.urlReembolsos('ig:1/2'), '/api/atendimento/conversas/ig%3A1%2F2/reembolsos', 'o id vai codificado')
    assert.equal(RC.urlAprovar('r-1'), '/api/atendimento/reembolsos/r-1/aprovar')
    assert.deepEqual(RC.MOTIVOS.map((m) => [m.v, m.l]), [
      ['defeito_avaria', 'Defeito ou avaria'],
      ['produto_errado', 'Produto errado ou cor trocada'],
      ['faltou_peca', 'Faltou peça'],
      ['frete', 'Frete'],
      ['outro', 'Outro'],
    ], 'a lista do dono, na ordem dele')
    assert.deepEqual(RC.FORMAS.map((f) => [f.v, f.l]), [['pix', 'Pix'], ['cupom', 'Cupom']])
    assert.equal(RC.rotuloMotivo('faltou_peca'), 'Faltou peça')
    assert.equal(RC.rotuloMotivo('motivo_novo'), 'motivo_novo', 'código novo: como veio')
    assert.equal(RC.rotuloMotivo(null), '')
    assert.equal(RC.rotuloForma('cupom'), 'Cupom')
    assert.equal(RC.rotuloForma(undefined), '')
    // As três situações desta entrega (o "Não aplicado" é da 4/6) e as cores do dono.
    assert.deepEqual(Object.keys(RC.SITUACOES), ['aguardando_adm', 'em_aberto', 'finalizado'])
    assert.equal(RC.situacaoInfo('aguardando_adm').rotulo, 'Aguardando ADM')
    assert.match(RC.situacaoInfo('aguardando_adm').cls, /amber/)
    assert.equal(RC.situacaoInfo('em_aberto').rotulo, 'Em aberto')
    assert.equal(RC.situacaoInfo('finalizado').rotulo, 'Finalizado')
    assert.match(RC.situacaoInfo('finalizado').cls, /emerald/)
    assert.equal(RC.situacaoInfo('nao_aplicado').rotulo, 'nao_aplicado', 'situação que a tela não conhece: o código')
    assert.ok(!('nao_aplicado' in RC.SITUACOES), '"Não aplicado" não entra nesta entrega')
    assert.deepEqual(RC.LIMITE_PADRAO, { valor: 100, pct: 25 })
  }

  // ------------------------------------------------ a resposta do GET sem buraco
  {
    const r = RC.respostaDe({ pedido: PEDIDO, itens: [item(), null, { semId: true }], pode_lancar: 1, motivo_bloqueio: '  ' })
    assert.equal(r.itens.length, 1, 'item sem id fica de fora')
    assert.equal(r.pode_lancar, false, 'só `true` libera')
    assert.equal(r.motivo_bloqueio, null)
    assert.deepEqual(r.limite, { valor: 100, pct: 25 }, 'sem limite, o do dono')
    assert.equal(RC.respostaDe(null).pedido, null)
    assert.deepEqual(RC.respostaDe({ limite: { valor: 80, pct: 20 } }).limite, { valor: 80, pct: 20 }, 'o do servidor manda')
    assert.equal(RC.respostaDe({ plataforma_detalhe: ' Shopee: Reembolso pago ao comprador ' }).plataforma_detalhe, 'Shopee: Reembolso pago ao comprador')
    assert.equal(RC.respostaDe({ plataforma_detalhe: '' }).plataforma_detalhe, null)
    // O que vai para o diálogo: sem o Pix, sem mexer na resposta do bloco.
    const comPix = RC.respostaDe({ pedido: PEDIDO, itens: [item({ dados_pagamento: PIX })] })
    const levado = RC.semPix(comPix)
    assert.equal(levado.itens[0].dados_pagamento, null)
    assert.equal(comPix.itens[0].dados_pagamento, PIX)
    assert.deepEqual(levado.pedido, PEDIDO)
    // O mesmo pedido? (a releitura do diálogo compara com o que o bloco leu, 17/11/2026)
    assert.equal(RC.mesmoPedido(PEDIDO, { ...PEDIDO, valor_pago: 10, conta: 'outra' }), true, 'o pedido é o número, não o valor')
    assert.equal(RC.mesmoPedido(PEDIDO, { ...PEDIDO, bling: '300102' }), false)
    assert.equal(RC.mesmoPedido(PEDIDO, { ...PEDIDO, marketplace: '251117XYZ' }), false)
    assert.equal(RC.mesmoPedido({ ...PEDIDO, bling: null }, { ...PEDIDO, bling: null }), true, 'ainda sem Bling: o da plataforma')
    assert.equal(RC.mesmoPedido(PEDIDO, null), false, 'a conversa perdeu o pedido')
    assert.equal(RC.mesmoPedido(null, undefined), true)
  }

  // ------------------------------------------------ o valor digitado
  {
    const v = (s) => RC.lerValor(s)
    assert.equal(v(''), null)
    assert.equal(v('   '), null)
    assert.deepEqual(v('50'), { valor: 50 })
    assert.deepEqual(v('50,9'), { valor: 50.9 })
    assert.deepEqual(v('50,90'), { valor: 50.9 })
    assert.deepEqual(v('50,'), { valor: 50 })
    assert.deepEqual(v('1.234,56'), { valor: 1234.56 })
    assert.deepEqual(v('1234.56'), { valor: 1234.56 })
    assert.deepEqual(v('R$ 89,90'), { valor: 89.9 })
    assert.deepEqual(v('1.234'), { valor: 1234 }, 'ponto com 3 casas é milhar')
    assert.ok('erro' in v('-5'))
    assert.ok('erro' in v('0'))
    assert.ok('erro' in v('0,00'))
    assert.ok('erro' in v('abc'))
    assert.ok('erro' in v('10,123'))
    assert.ok('erro' in v('1,2,3'))
    assert.equal(RC.valorNoCampo(1234.5), '1.234,50')
    assert.equal(esp(RC.moeda(189.9)), 'R$ 189,90')
    assert.equal(RC.moeda(null), '')
  }

  // ------------------------------------------------ o limite do ADM (o dono, 17/11/2026)
  {
    const L = { valor: 100, pct: 25 }
    const porques = (v, pago) => RC.porquesDoAdm(v, pago, L).map(esp)
    // 25% de R$ 189,90 = R$ 47,475: R$ 47,47 não passa; R$ 47,48 passa.
    assert.deepEqual(porques(40, 189.9), [])
    assert.deepEqual(porques(47.47, 189.9), [])
    assert.deepEqual(porques(47.48, 189.9), ['passa de 25% do valor pago (R$ 47,47)'])
    assert.deepEqual(porques(100, 1000), [], 'R$ 100 exatos não passa de R$ 100')
    assert.deepEqual(porques(100.01, 1000), ['passa de R$ 100,00'])
    assert.deepEqual(porques(120, 189.9), ['passa de R$ 100,00', 'passa de 25% do valor pago (R$ 47,47)'])
    assert.deepEqual(porques(189.9, 189.9), ['passa de R$ 100,00', 'é o valor inteiro que o comprador pagou'], 'o reembolso integral')
    assert.deepEqual(porques(30, 30), ['é o valor inteiro que o comprador pagou'], 'integral abaixo de R$ 100 também')
    assert.deepEqual(porques(25, 100), [], '25% exatos não passa')
    // Sem o valor pago não dá para conferir os 25%: o servidor manda para o ADM (o lado
    // seguro, `reembolso_cliente.requer_aprovacao`), e o aviso diz isso.
    assert.deepEqual(porques(80, null), ['sem o valor pago do pedido, o ADM confere'])
    assert.deepEqual(porques(150, 0), ['passa de R$ 100,00', 'sem o valor pago do pedido, o ADM confere'])
    // O ponto flutuante não engana: 0,1 + 0,2.
    assert.deepEqual(porques(0.1 + 0.2, 1.2), [], 'R$ 0,30 de R$ 1,20 é 25% exatos')
  }

  // ------------------------------------------------ o formulário e o corpo do POST
  {
    const f = (extra = {}) => ({ valorTexto: '48', motivo: 'defeito_avaria', detalhe: '', forma: 'pix', dadosPix: PIX, ...extra })
    assert.equal(RC.problemaDoForm(f(), 189.9), null)
    assert.equal(RC.problemaDoForm(f({ valorTexto: '' }), 189.9), 'Informe o valor do reembolso.')
    assert.equal(RC.problemaDoForm(f({ valorTexto: '0' }), 189.9), 'O valor tem de ser maior que zero.')
    assert.equal(esp(RC.problemaDoForm(f({ valorTexto: '189,91' }), 189.9)), 'O valor passa do que o comprador pagou (R$ 189,90).', 'nunca acima do pago')
    assert.equal(RC.problemaDoForm(f({ valorTexto: '189,90' }), 189.9), null, 'o valor inteiro pode (com o ADM)')
    assert.equal(RC.problemaDoForm(f({ valorTexto: '5000' }), null), null, 'sem o valor pago, o servidor confere o teto')
    assert.equal(RC.problemaDoForm(f({ motivo: '' }), 189.9), 'Escolha o motivo.')
    assert.equal(RC.problemaDoForm(f({ motivo: 'outro', detalhe: '  ' }), 189.9), 'Com o motivo "Outro", escreva o detalhe.')
    assert.equal(RC.problemaDoForm(f({ motivo: 'outro', detalhe: 'cliente pediu desconto' }), 189.9), null)
    assert.equal(RC.problemaDoForm(f({ detalhe: 'x'.repeat(RC.DETALHE_MAX + 1) }), 189.9), `O detalhe passa de ${RC.DETALHE_MAX} caracteres.`)
    assert.equal(RC.problemaDoForm(f({ forma: '' }), 189.9), 'Escolha a forma: Pix ou cupom.')
    assert.equal(RC.problemaDoForm(f({ dadosPix: ' ' }), 189.9), 'Preencha os dados do Pix: chave, banco e nome.')
    assert.equal(RC.problemaDoForm(f({ forma: 'cupom', dadosPix: '' }), 189.9), null, 'cupom não pede Pix')
    assert.equal(RC.problemaDoForm(f({ dadosPix: 'x'.repeat(RC.PIX_MAX + 1) }), 189.9), `Os dados do Pix passam de ${RC.PIX_MAX} caracteres.`)

    assert.deepEqual(RC.corpoDoReembolso(f({ valorTexto: '1.234,5', detalhe: '  veio riscado ' }), '300101'), {
      valor: 1234.5, motivo: 'defeito_avaria', motivo_detalhe: 'veio riscado', forma: 'pix', dados_pagamento: PIX, pedido_bling: '300101',
    })
    // No cupom os dados do Pix não vão, nem se ficaram escritos de antes.
    assert.deepEqual(RC.corpoDoReembolso(f({ forma: 'cupom' }), '300101'), {
      valor: 48, motivo: 'defeito_avaria', motivo_detalhe: null, forma: 'cupom', dados_pagamento: null, pedido_bling: '300101',
    })
    // Conta, plataforma e data não vão: o servidor tira da conversa. Do pedido, só o nº
    // do Bling que o diálogo mostrou, para o servidor conferir (17/11/2026).
    assert.deepEqual(Object.keys(RC.corpoDoReembolso(f(), '300101')).sort(), ['dados_pagamento', 'forma', 'motivo', 'motivo_detalhe', 'pedido_bling', 'valor'])
    assert.equal(RC.corpoDoReembolso(f(), null).pedido_bling, null, 'pedido ainda sem Bling (o servidor recusa com sem_pedido)')
    assert.equal(RC.corpoDoReembolso(f(), '').pedido_bling, null)
    assert.equal(RC.corpoDoReembolso(f({ valorTexto: 'x' }), '300101'), null)
    assert.equal(RC.corpoDoReembolso(f({ motivo: '' }), '300101'), null)
    assert.equal(RC.corpoDoReembolso(f({ valorTexto: '0,1' }), '300101').valor, 0.1)

    // O que conta como preenchido: o fundo escuro pergunta antes de descartar (17/11/2026).
    const vazio = { valorTexto: '', motivo: '', detalhe: '', forma: '', dadosPix: '' }
    assert.equal(RC.formPreenchido(vazio), false)
    assert.equal(RC.formPreenchido({ ...vazio, valorTexto: '  ', detalhe: ' ', dadosPix: '\n' }), false, 'só espaço não conta')
    for (const [k, v] of [['valorTexto', '5'], ['motivo', 'frete'], ['detalhe', 'x'], ['forma', 'cupom'], ['dadosPix', PIX]]) {
      assert.equal(RC.formPreenchido({ ...vazio, [k]: v }), true, k)
    }
  }

  // ------------------------------------------------ os textos de cada lançamento
  {
    assert.equal(RC.textoMotivo(item()), 'Defeito ou avaria: veio riscado')
    assert.equal(RC.textoMotivo(item({ motivo_detalhe: null })), 'Defeito ou avaria')
    assert.equal(RC.textoMotivo(item({ motivo: null, motivo_detalhe: null })), '')
    assert.equal(RC.textoLancado(item()), 'lançado por israel em 17/11 10:30', 'no horário de Brasília')
    assert.equal(RC.textoLancado(item({ criado_por: null })), 'lançado em 17/11 10:30', 'os antigos sem autor')
    assert.equal(RC.textoLancado(item({ criado_por: null, criado_em: null })), '')
    assert.equal(RC.textoAprovado(item()), '')
    assert.equal(RC.textoAprovado(item({ aprovado_por: 'joana dark', aprovado_em: '2026-11-17T14:00:00Z' })), 'aprovado por joana dark em 17/11 11:00')
    // O aviso dos anteriores (decisão 6: avisa, não bloqueia), de qualquer tipo.
    assert.equal(RC.avisoDeAnteriores([]), null)
    assert.equal(RC.avisoDeAnteriores(null), null)
    const um = RC.avisoDeAnteriores([item()])
    assert.equal(um.titulo, 'Este pedido já tem 1 reembolso lançado.')
    assert.deepEqual(um.linhas.map(esp), ['Cliente · R$ 50,00 · Em aberto · por israel'])
    const dois = RC.avisoDeAnteriores([item(), item({ id: 'r2', tipo: 'Manutenção', valor: 30, situacao: 'finalizado', criado_por: null })])
    assert.equal(dois.titulo, 'Este pedido já tem 2 reembolsos lançados.')
    assert.equal(esp(dois.linhas[1]), 'Manutenção · R$ 30,00 · Finalizado')
  }

  // ------------------------------------------------ os erros
  {
    const e422 = erroHttp(422, { code: 'valor_acima_do_pago', detail: 'O valor (R$ 200,00) passa do que o comprador pagou (R$ 189,90).' })
    assert.deepEqual(RC.erroDoReembolso(e422, P.erroDaApi(e422, 'x')), {
      code: 'valor_acima_do_pago', texto: 'O valor (R$ 200,00) passa do que o comprador pagou (R$ 189,90).', motivos: [],
    }, 'a frase do servidor manda')
    const soCodigo = erroHttp(403, { code: 'so_adm_plataforma_reembolsou' })
    assert.equal(RC.erroDoReembolso(soCodigo, P.erroDaApi(soCodigo, 'x')).texto, RC.ERROS_REEMBOLSO.so_adm_plataforma_reembolsou, 'sem frase: a da tela')
    for (const code of ['sem_pedido', 'valor_acima_do_pago', 'detalhe_obrigatorio', 'pix_sem_dados', 'sem_permissao', 'so_adm_plataforma_reembolsou', 'ja_aprovado', 'aguardando_adm', 'pedido_mudou']) {
      assert.ok(RC.ERROS_REEMBOLSO[code], `o código ${code} do contrato tem frase`)
    }
    // A validação do FastAPI (lista) e a rede: as frases de sempre da caixa.
    const val = erroHttp(422, [{ type: 'greater_than', loc: ['body', 'valor'], msg: 'Input should be greater than 0', ctx: { gt: 0 } }])
    const lidoVal = P.erroDaApi(val, 'Não consegui lançar o reembolso agora')
    assert.deepEqual(RC.erroDoReembolso(val, lidoVal), { code: null, texto: 'Não consegui lançar o reembolso agora', motivos: lidoVal.motivos })
    const rede = semRede()
    assert.match(RC.erroDoReembolso(rede, P.erroDaApi(rede, 'x')).motivos.join(' '), /sem conexão/)
    // As recusas que mudam o bloco (relê); a do valor não.
    for (const code of ['sem_pedido', 'sem_permissao', 'so_adm_plataforma_reembolsou', 'pedido_mudou']) assert.ok(RC.RELE_NA_RECUSA.has(code), code)
    assert.ok(!RC.RELE_NA_RECUSA.has('valor_acima_do_pago'))
  }

  // ------------------------------------------------ o atalho da triagem
  {
    const h = (assuntos) => ({ motivos: ['acao_pessoa'], rotulos: [], assuntos, assuntos_rotulos: [] })
    assert.equal(RC.pedeReembolso(h(['aprovar_fora_da_plataforma'])), true, 'Pix, reembolso parcial, "pago a diferença"')
    assert.equal(RC.pedeReembolso(h(['garantia', 'reembolso'])), true, 'o assunto reembolso')
    assert.equal(RC.pedeReembolso(h(['garantia'])), false)
    assert.equal(RC.pedeReembolso(h([])), false)
    assert.equal(RC.pedeReembolso({ motivos: [] }), false, 'API antiga sem assuntos')
    assert.equal(RC.pedeReembolso(null), false)
    // Os dois códigos são os do backend.
    const constantes = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/atendimento/constantes.py'), 'utf8')
    assert.match(constantes, /^ACAO_APROVAR_FORA_DA_PLATAFORMA = "aprovar_fora_da_plataforma"$/m)
    assert.ok(P.CATEGORIAS.reembolso, 'o assunto reembolso existe na taxonomia da tela')
  }

  testarContrato()
  await testarBloco()
  await testarDialogo()
  await testarPainel()
  await testarConversa()
}

// ================================================ o contrato com o backend (os dois lados juntos)
// Os tipos da tela = os schemas da API (schemas/atendimento_reembolso.py e o RefundOut), as
// rotas, os códigos das recusas, a lista de motivos, o limite e os tetos dos textos.
function testarContrato() {
  const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
  const schemas = api('schemas/atendimento_reembolso.py')
  const roteador = api('routers/atendimento_reembolso.py')
  const servico = api('services/atendimento/reembolso_conversa.py')
  const regras = api('services/reembolso_cliente.py')
  // Os campos de um schema pydantic, com os herdados.
  function campos(classe, fonte) {
    const m = fonte.match(new RegExp(`^class ${classe}\\((\\w+)\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) |$(?![\\s\\S]))`, 'm'))
    assert.ok(m, classe)
    const proprios = [...m[2].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
    return m[1] === 'BaseModel' ? proprios : [...campos(m[1], fonte), ...proprios]
  }
  const tipo = (nome, fonte = blocoSfc.fonte) => {
    const m = fonte.match(new RegExp(`(?:export )?type ${nome} = \\{\\n([\\s\\S]*?)\\n\\}`))
    assert.ok(m, nome)
    return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
  }
  const igual = (a, b, msg) => assert.deepEqual(a.slice().sort(), b.slice().sort(), msg)
  igual(tipo('PedidoReembolso'), campos('PedidoDoReembolsoOut', schemas), 'PedidoReembolso = PedidoDoReembolsoOut')
  igual(tipo('LimiteReembolso'), campos('LimiteDoReembolsoOut', schemas), 'LimiteReembolso = LimiteDoReembolsoOut')
  igual(tipo('ReembolsosResposta'), campos('ReembolsosDaConversaOut', schemas), 'ReembolsosResposta = ReembolsosDaConversaOut')
  igual(tipo('ReembolsoIn'), campos('LancarReembolsoIn', schemas), 'o corpo do POST = LancarReembolsoIn')
  // O item: a tela lê o que precisa (o servidor manda também a conta e a origem).
  const doItem = campos('ReembolsoItemOut', schemas)
  for (const c of tipo('ItemReembolso')) assert.ok(doItem.includes(c), `ItemReembolso.${c} vem da API`)
  // As listas (Literal) e as regras que as duas telas precisam concordar.
  const literal = (nome) => [...((schemas.match(new RegExp(`^${nome} = Literal\\[([^\\]]*)\\]$`, 'm')) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((x) => x[1])
  assert.deepEqual(literal('MotivoReembolso'), RC.MOTIVOS.map((m) => m.v), 'os motivos, na ordem do dono')
  assert.deepEqual(literal('FormaReembolso'), RC.FORMAS.map((f) => f.v))
  assert.deepEqual(literal('SituacaoReembolso'), Object.keys(RC.SITUACOES))
  const rotulos = Object.fromEntries([...((regras.match(/^MOTIVOS: dict\[str, str\] = \{([\s\S]*?)\n\}/m) || [])[1] || '').matchAll(/"([a-z_]+)": "([^"]+)"/g)].map((x) => [x[1], x[2]]))
  assert.deepEqual(rotulos, Object.fromEntries(RC.MOTIVOS.map((m) => [m.v, m.l])), 'os rótulos dos motivos = os do servidor')
  const numero = (nome) => Number((regras.match(new RegExp(`^${nome} = (\\d+)$`, 'm')) || [])[1])
  assert.deepEqual({ valor: numero('LIMITE_VALOR'), pct: numero('LIMITE_PCT') }, RC.LIMITE_PADRAO, 'o limite do ADM')
  assert.equal(RC.DETALHE_MAX, numero('MAX_DETALHE'))
  assert.equal(RC.PIX_MAX, numero('MAX_DADOS_PAGAMENTO'))
  // As rotas = as URLs da tela.
  assert.match(roteador, /prefix="\/api\/atendimento"/)
  assert.match(roteador, /@router\.get\("\/conversas\/\{conversa_id\}\/reembolsos"/)
  assert.match(roteador, /@router\.post\(\s*"\/conversas\/\{conversa_id\}\/reembolsos",/)
  assert.match(roteador, /@router\.post\("\/reembolsos\/\{refund_id\}\/aprovar"/)
  // Todo código que o servidor recusa tem a frase de reserva na tela.
  const codigos = new Set([...servico.matchAll(/ReembolsoRecusado\(\s*\d+,\s*"([a-z_]+)"/g)].map((x) => x[1]))
  assert.ok(codigos.size >= 8, `os códigos do serviço (${[...codigos]})`)
  for (const c of codigos) assert.ok(RC.ERROS_REEMBOLSO[c], `o código ${c} tem frase na tela`)
  // 17/11/2026: o corpo leva o `pedido_bling` que o diálogo mostrou (o ReembolsoIn acima
  // compara com o LancarReembolsoIn) e o servidor recusa com 409 pedido_mudou.
  assert.ok(codigos.has('pedido_mudou'), 'o servidor recusa quando a conversa está em outro pedido')
  // A tela Reembolso: o 409 `aguardando_adm` e as colunas novas do RefundOut.
  assert.match(api('routers/refunds.py'), /"code": "aguardando_adm"/)
  const refundOut = campos('RefundOut', api('schemas/refunds.py'))
  const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/reembolso.vue'), 'utf8')
  const linhaDaTela = tipo('RefundRow', pagina)
  for (const c of ['motivo', 'motivo_detalhe', 'forma_pagamento', 'dados_pagamento', 'requer_aprovacao', 'aprovado_por', 'aprovado_por_nome', 'aprovado_em', 'origem', 'conversa_id']) {
    assert.ok(linhaDaTela.includes(c), `RefundRow.${c}`)
    assert.ok(refundOut.includes(c), `RefundOut.${c}`)
  }
}

// ================================================ o bloco
const ICONES_BLOCO = ['BadgeCheck', 'Copy', 'HandCoins', 'Loader2', 'Lock', 'RotateCcw', 'TriangleAlert']
const RETORNO_BLOCO = ['props', 'dados', 'carregando', 'erro', 'aprovando', 'itens', 'DESTAQUE', 'carregar', 'lancar', 'aprovar', 'copiarPix']
function montarBloco(responde, props = {}) {
  const p = Vue.reactive({ conversaId: 'c1', sinal: 0, destaque: false, ...props })
  const falsa = apiFalsa(responde)
  const toasts = toastsFalsos()
  const emitidos = []
  const montados = []
  const copiados = []
  const estado = setupDe(blocoSfc, {
    ...RC,
    ref: Vue.ref,
    computed: Vue.computed,
    watch: Vue.watch,
    onMounted: (fn) => montados.push(fn),
    withDefaults: (x) => x,
    defineProps: () => p,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({ api: falsa.api }),
    useToasts: () => toasts,
    erroDaApi: P.erroDaApi,
    copiar: async (t) => { copiados.push(t); return true },
    window: { prompt: () => {} },
  }, RETORNO_BLOCO)
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...RC, ...estado, destaque: p.destaque }), render: renderDe(blocoSfc) })
    app.config.warnHandler = () => {}
    for (const n of ICONES_BLOCO) app.component(n, icone(n))
    return semComentarios(await renderToString(app))
  }
  return { props: p, estado, chamadas: falsa.chamadas, toasts: toasts.lista, emitidos, copiados, html, async montar() { for (const fn of montados) fn(); await esperar() } }
}

async function testarBloco() {
  // Lê na montagem: o GET da conversa, sem nada antes.
  {
    const m = montarBloco(() => resposta(), { conversaId: 'c:1' })
    assert.deepEqual(m.chamadas, [], 'nada antes de montar')
    let h = await m.html()
    assert.match(h, /data-reembolso-cliente/, 'é por aqui que o atalho acha o bloco')
    assert.match(h, /aria-label="Reembolso ao cliente"/)
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => [c.metodo, c.url]), [['GET', '/api/atendimento/conversas/c%3A1/reembolsos']])
    h = await m.html()
    const t = texto(h)
    assert.match(t, /Reembolso ao cliente/)
    assert.match(t, /Nenhum reembolso lançado neste pedido\./)
    const b = botao(h, 'data-reembolso-lancar')
    assert.ok(b && !desligado(b), 'pode lançar: o botão anda')
    assert.match(t, /Lançar reembolso ao cliente/)
    assert.doesNotMatch(h, /data-reembolso-bloqueio/)
    m.estado.lancar()
    assert.equal(m.emitidos.length, 1)
    assert.equal(m.emitidos[0][0], 'lancar')
    assert.deepEqual(m.emitidos[0][1].pedido, PEDIDO, 'o diálogo leva o que o bloco leu')
    // O destaque do atalho: o anel por um tempo.
    m.props.destaque = true
    assert.match(await m.html(), /ring-2 ring-primary\/70[^"]*"[^>]*data-reembolso-cliente/)
  }

  // A lista: valor, motivo, forma, situação em chip, quem lançou; o Pix nunca sem o ADM.
  {
    const itens = [
      item({ id: 'a', valor: 150, situacao: 'aguardando_adm', forma: 'pix' }),
      item({ id: 'b', valor: 20, motivo: 'frete', motivo_detalhe: null, forma: 'cupom', criado_por: 'marrocos' }),
      item({ id: 'c', tipo: 'Manutenção', valor: 30, motivo: null, motivo_detalhe: null, forma: null, situacao: 'finalizado', criado_por: null }),
      item({ id: 'd', valor: 120, situacao: 'em_aberto', aprovado_por: 'joana dark', aprovado_em: '2026-11-17T14:00:00Z' }),
    ]
    const m = montarBloco(() => resposta({ itens }))
    await m.montar()
    const h = await m.html()
    const t = texto(h)
    assert.deepEqual([...h.matchAll(/data-reembolso-item data-situacao="([^"]+)"/g)].map((x) => x[1]), ['aguardando_adm', 'em_aberto', 'finalizado', 'em_aberto'], 'na ordem do servidor')
    assert.match(t, /R\$ 150,00 Aguardando ADM Defeito ou avaria: veio riscado · Pix lançado por israel em 17\/11 10:30/)
    assert.match(t, /R\$ 20,00 Em aberto Frete · Cupom lançado por marrocos em 17\/11 10:30/)
    assert.match(t, /R\$ 30,00 Manutenção Finalizado lançado em 17\/11 10:30/, 'o tipo aparece fora do Cliente')
    assert.match(t, /aprovado por joana dark em 17\/11 11:00/)
    assert.equal((h.match(/title="tipo do reembolso/g) || []).length, 1, 'o Cliente não leva o selo do tipo')
    // As cores do dono: âmbar no Aguardando ADM, verde no Finalizado (nos dois temas).
    assert.match(h, /bg-amber-500\/20 text-amber-900 dark:text-amber-200[^>]*data-chip-situacao[^>]*>Aguardando ADM</)
    assert.match(h, /bg-emerald-500\/15 text-emerald-800 dark:text-emerald-300[^>]*data-chip-situacao[^>]*>Finalizado</)
    // Quem não é ADM: nem o Pix (vem null) nem o Aprovar.
    assert.doesNotMatch(h, /data-reembolso-pix/)
    assert.doesNotMatch(h, /data-reembolso-aprovar/)
    assert.doesNotMatch(h, /Nenhum reembolso lançado/)
    // Temas claro e escuro por tokens: nada de cor fixa solta.
    assert.doesNotMatch(blocoSfc.descriptor.template.content, /#[0-9a-f]{3,8}\b|bg-white|text-black/i)
  }

  // O ADM: o Pix (só ele recebe) com copiar, e o Aprovar só no que aguarda.
  {
    let gets = 0
    const itens = [item({ id: 'a', valor: 150, situacao: 'aguardando_adm', dados_pagamento: PIX }), item({ id: 'b', dados_pagamento: 'outro pix' })]
    const m = montarBloco((url, opts) => {
      if (opts && opts.method === 'POST') return { item: { ...itens[0], situacao: 'em_aberto', aprovado_por: 'joana dark', aprovado_em: '2026-11-17T14:00:00Z' } }
      gets += 1
      return resposta({ pode_aprovar: true, itens: gets === 1 ? itens : [{ ...itens[0], situacao: 'em_aberto', aprovado_por: 'joana dark', aprovado_em: '2026-11-17T14:00:00Z' }, itens[1]] })
    })
    await m.montar()
    let h = await m.html()
    assert.equal((h.match(/data-reembolso-pix/g) || []).length, 2, 'o Pix de cada um, para o ADM')
    assert.ok(h.includes(PIX))
    assert.equal((h.match(/data-reembolso-aprovar/g) || []).length, 1, 'só o que aguarda tem Aprovar')
    assert.match(h, /data-situacao="aguardando_adm"[\s\S]*data-reembolso-aprovar[\s\S]*data-situacao="em_aberto"/)
    await m.estado.copiarPix(PIX)
    assert.deepEqual(m.copiados, [PIX])
    // O "Lançar" leva ao diálogo a resposta SEM o Pix (ele só avisa dos anteriores).
    m.estado.lancar()
    assert.equal(m.emitidos[0][0], 'lancar')
    assert.ok(!JSON.stringify(m.emitidos).includes('Banco Exemplo'), 'o Pix não sai do bloco')
    assert.equal(m.emitidos[0][1].itens.length, 2, 'os anteriores vão (para o aviso)')
    assert.equal(m.estado.itens.value[0].dados_pagamento, PIX, 'o bloco continua com o Pix do ADM')
    await m.estado.aprovar(m.estado.itens.value[0])
    await esperar()
    assert.deepEqual(m.chamadas.map((c) => [c.metodo, c.url]), [
      ['GET', '/api/atendimento/conversas/c1/reembolsos'],
      ['POST', '/api/atendimento/reembolsos/a/aprovar'],
      ['GET', '/api/atendimento/conversas/c1/reembolsos'],
    ], 'aprova e relê')
    assert.equal(m.chamadas[1].body, undefined, 'o aprovar não leva corpo')
    h = await m.html()
    assert.doesNotMatch(h, /data-reembolso-aprovar/)
    assert.match(texto(h), /aprovado por joana dark em 17\/11 11:00/)
    assert.deepEqual(m.toasts.map((t) => [t[0], t[1]]), [['ok', 'Dados do Pix copiados'], ['ok', 'Reembolso aprovado']])
    assert.equal(esp(m.toasts[1][2]), 'R$ 150,00 liberado: quem paga marca Finalizado na tela Reembolso.')
    // O Pix nunca vai para toast.
    for (const t of m.toasts) assert.ok(!JSON.stringify(t).includes('Banco Exemplo'), 'o Pix não vai para o toast')
  }

  // Outro ADM aprovou antes (409 ja_aprovado): avisa e relê; erro de verdade: toast de erro.
  {
    const m = montarBloco((url, opts) => {
      if (opts && opts.method === 'POST') return erroHttp(409, { code: 'ja_aprovado' })
      return resposta({ pode_aprovar: true, itens: [item({ situacao: 'aguardando_adm' })] })
    })
    await m.montar()
    await m.estado.aprovar(m.estado.itens.value[0])
    await esperar()
    assert.deepEqual(m.toasts.map((t) => [t[0], t[1]]), [['info', RC.ERROS_REEMBOLSO.ja_aprovado]])
    assert.equal(m.chamadas.filter((c) => c.metodo === 'GET').length, 2, 'relê')
    const n = montarBloco((url, opts) => (opts && opts.method === 'POST' ? erroHttp(403, { code: 'forbidden' }) : resposta({ pode_aprovar: true, itens: [item({ situacao: 'aguardando_adm' })] })))
    await n.montar()
    await n.estado.aprovar(n.estado.itens.value[0])
    assert.equal(n.toasts[0][0], 'erro')
    assert.equal(n.estado.aprovando.value, null, 'o botão volta')
  }

  // Não pode lançar: desligado, com o porquê do servidor; a plataforma já reembolsou.
  {
    const motivo = 'A plataforma já reembolsou este pedido: só um ADM lança outro reembolso.'
    const m = montarBloco(() => resposta({ pode_lancar: false, motivo_bloqueio: motivo, plataforma_reembolsou: true, itens: [item()] }))
    await m.montar()
    const h = await m.html()
    const b = botao(h, 'data-reembolso-lancar')
    assert.ok(desligado(b), 'desligado')
    assert.ok(b.includes(`title="${motivo}"`), 'o porquê no title')
    assert.match(h, new RegExp(`data-reembolso-bloqueio[^>]*>${motivo}<`), 'e escrito embaixo')
    assert.match(texto(h), /A plataforma já devolveu dinheiro deste pedido ao comprador: confira para não pagar duas vezes \(só um ADM lança outro reembolso\)\./)
    m.estado.lancar()
    assert.deepEqual(m.emitidos, [], 'o clique não abre nada')
    // O ADM vê o aviso sem o "só um ADM", com o que a plataforma devolveu (a mesma fonte da aba).
    const a = montarBloco(() => resposta({ pode_aprovar: true, plataforma_reembolsou: true, plataforma_detalhe: 'Shopee: Reembolso pago ao comprador' }))
    await a.montar()
    const ta = texto(await a.html())
    assert.doesNotMatch(ta, /só um ADM lança outro/)
    assert.match(ta, /A plataforma já devolveu dinheiro deste pedido ao comprador \(Shopee: Reembolso pago ao comprador\): confira para não pagar duas vezes\./)
    // Sem o porquê do servidor: a frase de reserva.
    const s = montarBloco(() => resposta({ pode_lancar: false }))
    await s.montar()
    assert.ok(texto(await s.html()).includes(RC.ERROS_REEMBOLSO.sem_permissao))
  }

  // Sem pedido ligado: o porquê, sem a linha "nenhum reembolso".
  {
    const m = montarBloco(() => ({ pedido: null, plataforma_reembolsou: false, limite: { valor: 100, pct: 25 }, pode_lancar: false, motivo_bloqueio: 'A conversa não tem pedido ligado', pode_aprovar: false, itens: [] }))
    await m.montar()
    const h = await m.html()
    assert.match(h, /data-reembolso-bloqueio[^>]*>A conversa não tem pedido ligado</)
    assert.doesNotMatch(h, /Nenhum reembolso lançado/)
    assert.ok(desligado(botao(h, 'data-reembolso-lancar')))
  }

  // O GET falhou: o aviso com "tentar de novo"; a releitura que falha mantém o que havia.
  {
    let n = 0
    const m = montarBloco(() => {
      n += 1
      return n === 1 || n === 3 ? erroHttp(503, null) : resposta({ itens: [item()] })
    })
    await m.montar()
    let h = await m.html()
    assert.match(h, /data-reembolso-erro/)
    assert.match(texto(h), /Não consegui ler os reembolsos deste pedido agora tentar de novo/)
    assert.doesNotMatch(h, /data-reembolso-lancar/, 'sem resposta, sem botão')
    await m.estado.carregar()
    h = await m.html()
    assert.doesNotMatch(h, /data-reembolso-erro/)
    assert.match(h, /data-reembolso-item/)
    await m.estado.carregar()
    h = await m.html()
    assert.match(h, /data-reembolso-item/, 'a lista que havia fica')
    assert.match(h, /text-amber-800[^>]*data-reembolso-erro/, 'com o aviso da releitura')
  }

  // O sinal (lançou, o painel relido): relê. A resposta velha que chega depois não manda.
  {
    const pendentes = []
    const m = montarBloco(() => new Promise((r) => pendentes.push(r)))
    await m.montar()
    m.props.sinal = 1
    await esperar()
    assert.equal(m.chamadas.length, 2, 'o sinal relê')
    pendentes[1](resposta({ itens: [item({ id: 'nova' })] }))
    await esperar()
    pendentes[0](resposta({ itens: [item({ id: 'velha' })] }))
    await esperar()
    assert.deepEqual(m.estado.itens.value.map((i) => i.id), ['nova'], 'a resposta velha não sobrescreve')
    m.props.conversaId = 'c2'
    await esperar()
    assert.equal(m.chamadas[2].url, '/api/atendimento/conversas/c2/reembolsos')
  }

  // O bloco só lê e aprova: nunca lança (quem lança é o diálogo) nem fala com outra rota.
  {
    const fonte = blocoSfc.descriptor.scriptSetup.content
    assert.doesNotMatch(fonte, /method:\s*'(PUT|PATCH|DELETE)'/)
    assert.equal((fonte.match(/method:\s*'POST'/g) || []).length, 1, 'o único POST é o aprovar')
    assert.match(fonte, /api\(urlAprovar\(i\.id\), \{ method: 'POST' \}\)/)
  }
}

// ================================================ o diálogo
const ICONES_DIALOGO = ['HandCoins', 'Loader2', 'Lock', 'TriangleAlert', 'X']
const RETORNO_DIALOGO = ['props', 'aberto', 'valorTexto', 'motivo', 'detalhe', 'forma', 'dadosPix', 'enviando', 'erro', 'atual', 'relendo', 'falhaAoReler', 'travado', 'pedido', 'valorPago', 'limite', 'valor', 'porquesAdm', 'anteriores', 'problema', 'bloqueio', 'qualPedido', 'formatarValor', 'reler', 'lancar', 'campoValor', 'fechar', 'apertarFundo', 'clicarFundo']
// `responde` é o POST. O GET (a releitura ao abrir, 17/11/2026) devolve `get` ou, sem
// ele, o mesmo que o bloco leu. `confirma` é a resposta do confirm() do fundo escuro.
function montarDialogo(responde, dados = resposta(), { get, confirma = true } = {}) {
  const p = Vue.reactive({ conversaId: 'c1', dados })
  const aberto = Vue.ref(true)
  const falsa = apiFalsa((url, opts, n) => {
    if (opts && opts.method === 'POST') return responde(url, opts, n)
    return get ? get(url, opts, n) : dados
  })
  const toasts = toastsFalsos()
  const emitidos = []
  const perguntas = []
  const estado = setupDe(dialogoSfc, {
    ...RC,
    ref: Vue.ref,
    computed: Vue.computed,
    watch: Vue.watch,
    nextTick: Vue.nextTick,
    defineProps: () => p,
    defineModel: () => aberto,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({ api: falsa.api }),
    useToasts: () => toasts,
    erroDaApi: P.erroDaApi,
    statusDoErro: P.statusDoErro,
    confirm: (q) => { perguntas.push(q); return confirma },
  }, RETORNO_DIALOGO)
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...RC, ...estado, dados: p.dados }), render: renderDe(dialogoSfc) })
    app.config.warnHandler = () => {}
    for (const n of ICONES_DIALOGO) app.component(n, icone(n))
    return semComentarios(await renderToString(app))
  }
  const posts = () => falsa.chamadas.filter((c) => c.metodo === 'POST')
  return { props: p, aberto, estado, chamadas: falsa.chamadas, posts, toasts: toasts.lista, emitidos, perguntas, html }
}
// O mouse no fundo escuro: o alvo é o próprio fundo (FUNDO) ou algo dentro da caixa.
const FUNDO = { onde: 'fundo' }
const DENTRO = { onde: 'caixa' }
const evento = (alvo) => ({ target: alvo, currentTarget: FUNDO, impedido: false, preventDefault() { this.impedido = true } })
const cliqueNoFundo = (m) => {
  m.estado.apertarFundo(evento(FUNDO))
  m.estado.clicarFundo(evento(FUNDO))
}

async function testarDialogo() {
  // Aberto: relê o GET da conversa; os campos do dono, o pedido e por que ainda não lança.
  {
    const m = montarDialogo(() => ({ item: item() }))
    assert.deepEqual(m.chamadas.map((c) => [c.metodo, c.url]), [['GET', '/api/atendimento/conversas/c1/reembolsos']], 'abriu: relê')
    await esperar()
    let h = await m.html()
    let t = texto(h)
    assert.match(h, /data-dialogo-reembolso/)
    assert.match(h, /role="dialog" aria-modal="true" aria-label="Lançar reembolso ao cliente" tabindex="-1"/)
    assert.match(t, /Lançar reembolso ao cliente Pedido 251117ABC · Bling 300101 · Shopee ATV/, 'de qual pedido é (o da conversa)')
    assert.match(h, /id="reembolso-valor"[^>]*inputmode="decimal"[^>]*autocomplete="off"[^>]*placeholder="0,00"/)
    assert.match(h, /data-valor-pago[^>]*>de R\$.189,90 pagos</, 'mostra "de R$ X pagos"')
    // O "escolha o motivo…" vazio (o SSR escreve `value` sem o ="") e as 5 do dono.
    assert.deepEqual([...h.matchAll(/<option value(?:="([^"]*)")?/g)].map((x) => x[1] ?? ''), ['', 'defeito_avaria', 'produto_errado', 'faltou_peca', 'frete', 'outro'])
    assert.match(h, /<option value disabled>escolha o motivo…<\/option>/)
    assert.deepEqual([...h.matchAll(/data-forma="([^"]+)"/g)].map((x) => x[1]), ['pix', 'cupom'])
    assert.doesNotMatch(h, /data-campo-pix/, 'sem a forma Pix, sem o campo do Pix')
    assert.doesNotMatch(h, /data-aviso-anteriores|data-aviso-adm|data-aviso-plataforma|data-erro-reembolso|data-aviso-releitura/)
    let b = botao(h, 'data-botao-lancar')
    assert.ok(desligado(b))
    assert.ok(b.includes('title="Informe o valor do reembolso."'))
    assert.doesNotMatch(b, /data-trava/)
    assert.match(h, /data-motivo-sem-lancar[^>]*>Informe o valor do reembolso\.</)
    await m.estado.lancar()
    assert.equal(m.posts().length, 0, 'o clique sem o formulário completo não sai')

    // O limite do ADM, ao vivo: 25% de R$ 189,90 = R$ 47,47.
    m.estado.valorTexto.value = '40'
    h = await m.html()
    assert.doesNotMatch(h, /data-aviso-adm/)
    m.estado.valorTexto.value = '48'
    h = await m.html()
    assert.match(texto(h), /Vai precisar da aprovação de um ADM \(passa de 25% do valor pago \(R\$ 47,47\)\)\. Até lá fica "Aguardando ADM" e não pode ser pago\./)
    m.estado.valorTexto.value = '120'
    assert.match(texto(await m.html()), /\(passa de R\$ 100,00; passa de 25% do valor pago \(R\$ 47,47\)\)/)
    m.estado.valorTexto.value = '189,90'
    assert.match(texto(await m.html()), /\(passa de R\$ 100,00; é o valor inteiro que o comprador pagou\)/)
    m.estado.valorTexto.value = '200'
    h = await m.html()
    assert.ok(desligado(botao(h, 'data-botao-lancar')), 'acima do pago não lança')
    assert.match(texto(h), /O valor passa do que o comprador pagou \(R\$ 189,90\)\./)

    // O motivo "Outro" pede o detalhe; a forma Pix pede os dados, num campo protegido.
    m.estado.valorTexto.value = '48'
    m.estado.motivo.value = 'outro'
    m.estado.forma.value = 'pix'
    h = await m.html()
    assert.ok(botao(h, 'data-botao-lancar').includes('title="Com o motivo &quot;Outro&quot;, escreva o detalhe."'))
    assert.match(h, /placeholder="Escreva o motivo \(obrigatório no Outro\)"/)
    m.estado.detalhe.value = 'cliente pediu a diferença de frete'
    h = await m.html()
    assert.match(h, /data-campo-pix/)
    assert.match(h, /id="reembolso-pix"[^>]*autocomplete="off"[^>]*spellcheck="false"[^>]*data-lpignore="true"[^>]*data-1p-ignore="true"/, 'sem autocompletar nem corretor')
    assert.match(texto(h), /Campo protegido: só o ADM vê estes dados \(na conversa e na tela Reembolso\)\. Eles não saem no Excel\./)
    assert.ok(botao(h, 'data-botao-lancar').includes('title="Preencha os dados do Pix: chave, banco e nome."'))
    m.estado.dadosPix.value = PIX
    h = await m.html()
    b = botao(h, 'data-botao-lancar')
    assert.ok(!desligado(b), 'completo: lança')
    assert.doesNotMatch(h, /data-motivo-sem-lancar/)

    // Ao sair do campo, o valor fica escrito do jeito certo.
    m.estado.valorTexto.value = '1234,5'
    m.estado.formatarValor()
    assert.equal(m.estado.valorTexto.value, '1.234,50')
    m.estado.valorTexto.value = 'abc'
    m.estado.formatarValor()
    assert.equal(m.estado.valorTexto.value, 'abc', 'o que não é valor fica como está (o aviso diz o quê)')
    m.estado.valorTexto.value = '48'

    // Lança: o corpo (com o Bling que o diálogo mostrou), o toast (sem o Pix), fecha,
    // limpa o Pix e avisa o painel.
    await m.estado.lancar()
    assert.deepEqual(m.posts(), [{
      url: '/api/atendimento/conversas/c1/reembolsos', metodo: 'POST',
      body: { valor: 48, motivo: 'outro', motivo_detalhe: 'cliente pediu a diferença de frete', forma: 'pix', dados_pagamento: PIX, pedido_bling: '300101' },
    }])
    assert.equal(m.aberto.value, false, 'fecha')
    assert.equal(m.estado.dadosPix.value, '', 'o Pix não fica na memória da tela')
    assert.deepEqual(m.emitidos, [['lancado']], 'o painel relê; o evento não leva dado nenhum')
    assert.deepEqual(m.toasts.map((x) => [x[0], x[1], x[2]]), [['ok', 'Reembolso ao cliente lançado', 'Em aberto: quem paga marca Finalizado na tela Reembolso.']])
    for (const x of m.toasts) assert.ok(!JSON.stringify(x).includes('Banco Exemplo'))
    assert.equal(await m.html(), '<!--v-if-->'.replace(/<!--[\s\S]*?-->/g, ''), 'fechado, nada na tela')
  }

  // A releitura ao abrir (17/11/2026): o bloco só relê a cada 2 min, e outra pessoa
  // pode ter lançado nesse meio-tempo. O "Lançar" espera a leitura.
  {
    let soltarGet
    const outro = item({ id: 'r9', valor: 30, criado_por: 'marrocos', dados_pagamento: PIX })
    const m = montarDialogo(() => ({ item: item() }), resposta(), { get: () => new Promise((r) => { soltarGet = r }) })
    m.estado.valorTexto.value = '20'
    m.estado.motivo.value = 'frete'
    m.estado.forma.value = 'cupom'
    let h = await m.html()
    let b = botao(h, 'data-botao-lancar')
    assert.ok(desligado(b), 'lendo: ainda não lança')
    assert.ok(b.includes('title="Conferindo os reembolsos que o pedido já tem…"'))
    assert.match((h.match(/<button[^>]*data-botao-lancar[^>]*>[\s\S]*?<\/button>/) || [''])[0], /data-icone="Loader2"/, 'o botão gira')
    assert.doesNotMatch(h, /data-aviso-anteriores/, 'o bloco não tinha nenhum')
    await m.estado.lancar()
    assert.equal(m.posts().length, 0)
    soltarGet(resposta({ pode_aprovar: true, itens: [outro] }))
    await esperar()
    h = await m.html()
    assert.match(texto(h), /Este pedido já tem 1 reembolso lançado\. Cliente · R\$ 30,00 · Em aberto · por marrocos/, 'o que a outra pessoa lançou aparece')
    assert.ok(!h.includes('Banco Exemplo'), 'o Pix do ADM não entra no diálogo')
    assert.equal(m.estado.atual.value.itens[0].dados_pagamento, null)
    b = botao(h, 'data-botao-lancar')
    assert.ok(!desligado(b), 'lido: lança')
    assert.deepEqual(m.emitidos, [], 'o mesmo pedido: o bloco não precisa reler')
    await m.estado.lancar()
    assert.equal(m.posts()[0].body.pedido_bling, '300101')
  }

  // A conversa já estava em outro pedido: o diálogo mostra o de agora (é o Bling que
  // vai no POST, com o teto dele) e o bloco relê.
  {
    const novo = { ...PEDIDO, bling: '300102', marketplace: '251117XYZ', valor_pago: 40 }
    const m = montarDialogo(() => ({ item: item() }), resposta(), { get: () => resposta({ pedido: novo }) })
    await esperar()
    assert.deepEqual(m.emitidos, [['recarregar']])
    const h = await m.html()
    assert.match(texto(h), /Lançar reembolso ao cliente Pedido 251117XYZ · Bling 300102 · Shopee ATV/)
    assert.match(h, /data-valor-pago[^>]*>de R\$.40,00 pagos</, 'o teto do pedido de agora')
    m.estado.valorTexto.value = '20'
    m.estado.motivo.value = 'frete'
    m.estado.forma.value = 'cupom'
    await m.estado.lancar()
    assert.equal(m.posts()[0].body.pedido_bling, '300102')
  }

  // A releitura falhou: fica o que o bloco leu, com o aviso; o servidor confere no POST.
  {
    const m = montarDialogo(() => ({ item: item() }), resposta({ itens: [item()] }), { get: () => erroHttp(503, null) })
    await esperar()
    const h = await m.html()
    assert.match(h, /text-amber-800[^>]*data-aviso-releitura/)
    assert.match(texto(h), /Não consegui reler os reembolsos do pedido agora: os avisos abaixo são do que o bloco tinha lido\./)
    assert.match(h, /data-aviso-anteriores/, 'o aviso do que o bloco leu fica')
    m.estado.valorTexto.value = '20'
    m.estado.motivo.value = 'frete'
    m.estado.forma.value = 'cupom'
    assert.equal(m.estado.bloqueio.value, null, 'lança mesmo assim')
    await m.estado.lancar()
    assert.equal(m.posts()[0].body.pedido_bling, '300101')
    assert.deepEqual(m.emitidos, [['lancado']])
  }

  // A releitura diz que a pessoa não lança mais (a plataforma reembolsou nesse meio-tempo):
  // desligado, com o porquê do servidor.
  {
    const motivo = 'A plataforma já reembolsou este pedido: só um ADM lança outro reembolso.'
    const m = montarDialogo(() => ({ item: item() }), resposta(), { get: () => resposta({ pode_lancar: false, motivo_bloqueio: motivo, plataforma_reembolsou: true }) })
    await esperar()
    m.estado.valorTexto.value = '20'
    m.estado.motivo.value = 'frete'
    m.estado.forma.value = 'cupom'
    const h = await m.html()
    const b = botao(h, 'data-botao-lancar')
    assert.ok(desligado(b))
    assert.ok(b.includes(`title="${motivo}"`))
    assert.match(h, /data-aviso-plataforma/)
    await m.estado.lancar()
    assert.equal(m.posts().length, 0)
  }

  // A releitura que chega depois de fechar não vale.
  {
    let soltarGet
    const m = montarDialogo(() => ({}), resposta(), { get: () => new Promise((r) => { soltarGet = r }) })
    m.estado.fechar()
    soltarGet(resposta({ pedido: { ...PEDIDO, bling: '999' } }))
    await esperar()
    assert.deepEqual(m.emitidos, [], 'ninguém relê por causa dela')
    assert.equal(m.estado.atual.value.pedido.bling, '300101')
  }

  // Acima do limite: o toast diz que espera o ADM; o ADM lê que já sai aprovado.
  {
    const m = montarDialogo(() => ({ item: item({ valor: 150, situacao: 'aguardando_adm' }) }))
    await esperar()
    m.estado.valorTexto.value = '150'
    m.estado.motivo.value = 'defeito_avaria'
    m.estado.forma.value = 'cupom'
    m.estado.dadosPix.value = 'ficou de antes'
    assert.equal(m.estado.problema.value, null)
    await m.estado.lancar()
    assert.equal(m.posts()[0].body.dados_pagamento, null, 'cupom: os dados do Pix não vão')
    assert.equal(m.toasts[0][2], 'Aguardando a aprovação de um ADM antes de pagar.')
    const adm = montarDialogo(() => ({}), resposta({ pode_aprovar: true }))
    await esperar()
    adm.estado.valorTexto.value = '150'
    assert.match(texto(await adm.html()), /Passa do limite \(passa de R\$ 100,00; passa de 25% do valor pago \(R\$ 47,47\)\), mas você é ADM: o reembolso já sai aprovado no seu nome\./)
  }

  // Os avisos que não bloqueiam: o pedido já tem reembolso; a plataforma já devolveu.
  {
    const dados = resposta({ plataforma_reembolsou: true, pode_aprovar: true, itens: [item(), item({ id: 'r2', tipo: 'Manutenção', valor: 30, situacao: 'finalizado', criado_por: null })] })
    const m = montarDialogo(() => ({}), dados)
    await esperar()
    const h = await m.html()
    const t = texto(h)
    assert.match(h, /role="alert"[^>]*data-aviso-anteriores/)
    assert.match(t, /Este pedido já tem 2 reembolsos lançados\. Cliente · R\$ 50,00 · Em aberto · por israel Manutenção · R\$ 30,00 · Finalizado Confira antes de lançar outro/)
    assert.match(t, /A plataforma já devolveu dinheiro deste pedido ao comprador: confira para não pagar duas vezes\./)
    const comDetalhe = montarDialogo(() => ({}), { ...dados, plataforma_detalhe: 'Mercado Livre: dinheiro devolvido ao comprador' })
    await esperar()
    assert.match(texto(await comDetalhe.html()), /ao comprador \(Mercado Livre: dinheiro devolvido ao comprador\): confira/)
    // Sem o valor pago: o servidor confere o teto e o ADM aprova (o aviso já diz).
    const s = montarDialogo(() => ({}), resposta({ pedido: { ...PEDIDO, valor_pago: null } }))
    await esperar()
    s.estado.valorTexto.value = '20'
    const ts_ = texto(await s.html())
    assert.match(ts_, /o valor pago não veio: o servidor confere o teto/)
    assert.match(ts_, /Vai precisar da aprovação de um ADM \(sem o valor pago do pedido, o ADM confere\)/)
  }

  // As recusas: a frase dentro do diálogo, que fica aberto; as que mudam o bloco, relê.
  {
    const preencher = (m) => {
      m.estado.valorTexto.value = '48'
      m.estado.motivo.value = 'defeito_avaria'
      m.estado.forma.value = 'pix'
      m.estado.dadosPix.value = PIX
    }
    const v = montarDialogo(() => erroHttp(422, { code: 'valor_acima_do_pago', detail: 'O valor passa do que o comprador pagou (R$ 40,00).' }))
    await esperar()
    preencher(v)
    await v.estado.lancar()
    assert.equal(v.aberto.value, true, 'continua aberto')
    let h = await v.html()
    assert.match(h, /data-erro-reembolso data-code="valor_acima_do_pago"/)
    assert.ok(texto(h).includes('O valor passa do que o comprador pagou (R$ 40,00).'))
    assert.deepEqual(v.emitidos, [], 'a recusa do valor não relê o bloco')
    assert.equal(v.estado.dadosPix.value, PIX, 'o que foi digitado fica para corrigir')
    assert.equal(v.toasts.length, 0)
    assert.equal(v.estado.travado.value, null, 'corrigir e lançar de novo pode')

    const r = montarDialogo(() => erroHttp(403, { code: 'so_adm_plataforma_reembolsou' }))
    await esperar()
    preencher(r)
    await r.estado.lancar()
    assert.deepEqual(r.emitidos, [['recarregar']], 'quem pode mudou: o bloco relê')
    assert.ok(texto(await r.html()).includes(RC.ERROS_REEMBOLSO.so_adm_plataforma_reembolsou))

    // A conversa mudou de pedido depois que o diálogo abriu (17/11/2026): o servidor
    // recusa com 409 pedido_mudou. A frase dele aparece, o bloco relê e este diálogo
    // não lança mais (o cabeçalho segue no pedido em que a pessoa preencheu).
    const frase = 'O pedido da conversa mudou (agora é o Bling 300102): abra o lançamento de novo.'
    const x = montarDialogo(() => erroHttp(409, { code: 'pedido_mudou', detail: frase }))
    await esperar()
    preencher(x)
    await x.estado.lancar()
    assert.equal(x.posts()[0].body.pedido_bling, '300101', 'levou o Bling que o diálogo mostrou')
    h = await x.html()
    assert.match(h, /data-erro-reembolso data-code="pedido_mudou"/)
    assert.ok(texto(h).includes(frase), 'a frase do servidor')
    assert.match(texto(h), /Lançar reembolso ao cliente Pedido 251117ABC · Bling 300101/)
    assert.deepEqual(x.emitidos, [['recarregar']], 'o bloco relê')
    let b = botao(h, 'data-botao-lancar')
    assert.ok(desligado(b))
    assert.ok(b.includes('data-trava="pedido_mudou"'))
    assert.ok(b.includes('title="A conversa está em outro pedido: feche e abra o lançamento de novo."'))
    await x.estado.lancar()
    assert.equal(x.posts().length, 1, 'não tenta de novo no pedido velho')
    // Sem a frase do servidor: a da tela. Fechar pelo fundo não pergunta (o que foi
    // digitado não serve mais) e não relê de novo (já releu na recusa).
    const y = montarDialogo(() => erroHttp(409, { code: 'pedido_mudou' }))
    await esperar()
    preencher(y)
    await y.estado.lancar()
    assert.ok(texto(await y.html()).includes(RC.ERROS_REEMBOLSO.pedido_mudou))
    cliqueNoFundo(y)
    assert.equal(y.aberto.value, false)
    assert.deepEqual(y.perguntas, [])
    assert.deepEqual(y.emitidos, [['recarregar']])

    // A resposta não chegou: o reembolso pode ter entrado. O "Lançar" fica desligado até
    // fechar, e ao fechar o bloco relê a lista (17/11/2026).
    for (const falha of [semRede(), erroHttp(502, null)]) {
      const n = montarDialogo(() => falha)
      await esperar()
      preencher(n)
      await n.estado.lancar()
      h = await n.html()
      assert.match(texto(h), /Não sei se o reembolso entrou: a resposta do servidor não chegou\. Feche e confira a lista do pedido antes de lançar de novo\./)
      assert.equal(n.estado.enviando.value, false)
      assert.equal(n.estado.travado.value, 'incerto')
      b = botao(h, 'data-botao-lancar')
      assert.ok(desligado(b), 'o Lançar fica desligado')
      assert.ok(b.includes('data-trava="incerto"'))
      assert.ok(b.includes('title="Feche e confira a lista do pedido: o reembolso pode ter entrado."'))
      assert.deepEqual(n.emitidos, [], 'o bloco relê ao fechar, não antes')
      await n.estado.lancar()
      assert.equal(n.posts().length, 1, 'o clique de novo não manda outro POST')
      // Mexer nos campos não religa o botão.
      n.estado.valorTexto.value = '47'
      assert.ok(desligado(botao(await n.html(), 'data-botao-lancar')))
      n.estado.fechar()
      assert.equal(n.aberto.value, false)
      assert.deepEqual(n.emitidos, [['recarregar']], 'fechou: o bloco relê a lista')
      assert.equal(n.estado.dadosPix.value, '')
      await esperar()
      // Reaberto: em branco e destravado.
      n.aberto.value = true
      await esperar()
      assert.equal(n.estado.travado.value, null)
      assert.equal(n.estado.erro.value, null)
      assert.equal(n.estado.bloqueio.value, 'Informe o valor do reembolso.')
    }
    // Fechar pelo fundo depois da resposta que não chegou: não pergunta e o bloco relê.
    const z = montarDialogo(() => semRede())
    await esperar()
    preencher(z)
    await z.estado.lancar()
    cliqueNoFundo(z)
    assert.equal(z.aberto.value, false)
    assert.deepEqual(z.perguntas, [])
    assert.deepEqual(z.emitidos, [['recarregar']])
  }

  // O fundo escuro (17/11/2026): só o clique que começa e termina nele fecha; com algo
  // preenchido, pergunta antes de descartar.
  {
    const m = montarDialogo(() => ({}), resposta(), { confirma: false })
    await esperar()
    // Vazio: o clique no fundo fecha sem perguntar; o mousedown no fundo não tira o foco
    // da caixa (o Esc segue valendo se a pessoa desistir).
    const desce = evento(FUNDO)
    m.estado.apertarFundo(desce)
    assert.equal(desce.impedido, true)
    m.estado.clicarFundo(evento(FUNDO))
    assert.equal(m.aberto.value, false)
    assert.deepEqual(m.perguntas, [])
    await esperar()
    m.aberto.value = true
    await esperar()
    m.estado.valorTexto.value = '48'
    m.estado.motivo.value = 'defeito_avaria'
    m.estado.forma.value = 'pix'
    m.estado.dadosPix.value = PIX
    // O arrasto que começa num campo e solta no fundo: o click cai no fundo, mas não fecha.
    const dentro = evento(DENTRO)
    m.estado.apertarFundo(dentro)
    assert.equal(dentro.impedido, false, 'dentro da caixa o mousedown segue normal (seleção de texto)')
    m.estado.clicarFundo(evento(FUNDO))
    assert.equal(m.aberto.value, true, 'não fecha')
    assert.deepEqual(m.perguntas, [], 'nem pergunta')
    // O clique dentro da caixa (que sobe até o fundo) também não.
    m.estado.apertarFundo(evento(DENTRO))
    m.estado.clicarFundo(evento(DENTRO))
    assert.equal(m.aberto.value, true)
    // O clique inteiro no fundo com algo preenchido: pergunta; "Cancelar" mantém tudo.
    cliqueNoFundo(m)
    assert.deepEqual(m.perguntas, ['Fechar sem lançar? O que você preencheu neste reembolso se perde.'])
    assert.equal(m.aberto.value, true)
    assert.equal(m.estado.dadosPix.value, PIX, 'o Pix digitado continua')
    // O mousedown velho no fundo não vale para o click de um arrasto depois.
    m.estado.apertarFundo(evento(FUNDO))
    m.estado.apertarFundo(evento(DENTRO))
    m.estado.clicarFundo(evento(FUNDO))
    assert.equal(m.perguntas.length, 1)
    // "OK": fecha e limpa o Pix; qualquer campo preenchido pergunta.
    const ok = montarDialogo(() => ({}), resposta(), { confirma: true })
    await esperar()
    ok.estado.detalhe.value = 'só o detalhe'
    ok.estado.dadosPix.value = PIX
    cliqueNoFundo(ok)
    assert.equal(ok.perguntas.length, 1)
    assert.equal(ok.aberto.value, false)
    assert.equal(ok.estado.dadosPix.value, '')
    assert.deepEqual(ok.emitidos, [], 'fechar sem lançar não relê o bloco')
    // No meio do envio, o fundo não fecha nem pergunta.
    let soltar
    const e = montarDialogo(() => new Promise((r) => { soltar = r }))
    await esperar()
    e.estado.valorTexto.value = '48'
    e.estado.motivo.value = 'frete'
    e.estado.forma.value = 'cupom'
    const indo = e.estado.lancar()
    await esperar()
    cliqueNoFundo(e)
    assert.equal(e.aberto.value, true)
    assert.deepEqual(e.perguntas, [])
    soltar({ item: item() })
    await indo
    assert.equal(e.aberto.value, false)
  }

  // Durante o envio não fecha; reabrir começa em branco; fechar apaga o Pix.
  {
    let soltar
    const m = montarDialogo(() => new Promise((r) => { soltar = r }))
    await esperar()
    m.estado.valorTexto.value = '48'
    m.estado.motivo.value = 'frete'
    m.estado.forma.value = 'pix'
    m.estado.dadosPix.value = PIX
    const indo = m.estado.lancar()
    await esperar()
    assert.equal(m.estado.enviando.value, true)
    await m.estado.lancar()
    assert.equal(m.posts().length, 1, 'o clique repetido não manda outro')
    m.estado.fechar()
    assert.equal(m.aberto.value, true, 'no meio do envio não fecha')
    assert.ok(desligado(botao(await m.html(), 'data-botao-lancar')))
    soltar({ item: item() })
    await indo
    assert.equal(m.aberto.value, false)
    m.aberto.value = true
    await esperar()
    for (const k of ['valorTexto', 'motivo', 'detalhe', 'forma', 'dadosPix']) assert.equal(m.estado[k].value, '', `${k} em branco ao reabrir`)
    assert.equal(m.chamadas.filter((c) => c.metodo === 'GET').length, 2, 'reaberto: relê de novo')
    m.estado.dadosPix.value = 'x'
    m.estado.fechar()
    assert.equal(m.estado.dadosPix.value, '')
  }

  // O diálogo só fala com a rota dos reembolsos da conversa e nunca manda conta.
  {
    const fonte = dialogoSfc.descriptor.scriptSetup.content
    assert.equal((fonte.match(/api</g) || []).length, 2, 'duas chamadas: a releitura (GET) e o POST')
    assert.match(fonte, /semPix\(respostaDe\(await api<ReembolsosResposta>\(urlReembolsos\(props\.conversaId\)\)\)\)/, 'a releitura sem o Pix')
    assert.match(fonte, /api<\{ item\?: ItemReembolso \}>\(urlReembolsos\(props\.conversaId\), \{ method: 'POST', body: corpo \}\)/)
    assert.match(fonte, /corpoDoReembolso\(form\.value, pedido\.value\?\.bling \?\? null\)/, 'o Bling que o diálogo mostra')
    assert.doesNotMatch(fonte, /console\./, 'nada vai para o log')
    const tpl = dialogoSfc.descriptor.template.content
    assert.doesNotMatch(tpl, /#[0-9a-f]{3,8}\b|bg-white|text-black/i)
    // Sem <form>: o Enter num campo não lança dinheiro sozinho.
    assert.doesNotMatch(tpl, /<form/)
    // O Esc vale mesmo depois de um clique num texto da caixa: a caixa recebe o foco
    // (tabindex -1, sem contorno), como a do AtendimentoTroca (17/11/2026).
    assert.match(tpl, /role="dialog"[^>]*tabindex="-1"[^>]*class="[^"]*\boutline-none\b/)
    assert.match(tpl, /data-dialogo-reembolso\s+@mousedown="apertarFundo"\s+@click="clicarFundo"\s+@keydown\.esc\.stop="fechar"/)
    assert.doesNotMatch(tpl, /@click\.self/, 'o fundo não fecha com qualquer click (o arrasto que solta fora)')
  }
}

// ================================================ o painel do pedido
const REL_PEDIDO = '../components/AtendimentoPedido.vue'
function montarPainel(props, { filhos = {}, timers } = {}) {
  const { descriptor, filename } = sfc(REL_PEDIDO)
  const script = compileScript(descriptor, { id: 'AtendimentoPedido.vue', inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'AtendimentoPedido.vue', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const avisos = []
  const emitidos = []
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: () => {}, onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: async (url) => { throw new Error(`rota inesperada: ${url}`) } }),
    useToasts: () => ({ success: () => {}, error: () => {}, info: (t, l) => avisos.push([t, l]), warning: () => {} }),
    localStorage: { getItem: () => null, setItem: () => {} },
    window: { prompt: () => {}, addEventListener: () => {}, removeEventListener: () => {} },
    setTimeout: timers ? timers.setTimeout : setTimeout,
    clearTimeout: timers ? timers.clearTimeout : clearTimeout,
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  const exposto = {}
  const aviso = console.warn
  console.warn = () => {}
  let bruto
  try {
    bruto = comp.setup(reativo, { emit: (...a) => emitidos.push(a), expose: (x) => Object.assign(exposto, x), attrs: {}, slots: {} })
  } finally {
    console.warn = aviso
  }
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, exposto, avisos, emitidos, props: reativo }
}

async function testarPainel() {
  const filhos = {
    AtendimentoReembolsoCliente: {
      props: ['conversaId', 'sinal', 'destaque'],
      setup: (p) => () => Vue.h('section', { 'data-bloco-reembolso': p.conversaId, 'data-sinal': String(p.sinal), 'data-destaque': String(p.destaque) }, 'bloco do reembolso'),
    },
    AtendimentoReembolsoLancar: {
      props: ['conversaId', 'dados', 'aberto'],
      setup: (p) => () => Vue.h('div', { 'data-dialogo-falso': p.conversaId, 'data-aberto': String(p.aberto) }),
    },
  }
  const conversa = (extra = {}) => ({ id: 'c1', plataforma: 'shopee', canal: 'chat', pedido_marketplace: '251117ABC', somente_leitura: false, email: null, ...extra })
  const retrato = (pedido) => ({
    pedido, moeda: 'BRL', status: 'COMPLETED', status_texto: 'Concluído', itens: [{ titulo: `Produto do ${pedido}`, preco: 189.9, quantidade: 1 }],
    valor_pago: 189.9, total: 189.9, frete: 0, pagamento_metodo: 'Pix', pago_em: null, criado_em: null, enviado_em: null, concluido_em: null,
    nf: { numero: '123', status: null }, logistica: null, atualizado_em: null,
  })
  const base = { contexto: null, produto: null, produtosConversa: [], canEdit: true, painel: null }

  // Com retrato: logo abaixo de "Valor pago pelo comprador" (depois dos valores, antes da NF).
  let m = montarPainel({ ...base, conversa: conversa(), pedidoMkt: retrato('251117ABC') }, { filhos })
  let h = await m.html()
  assert.equal((h.match(/data-bloco-reembolso=/g) || []).length, 1, 'um bloco só')
  assert.match(h, /data-bloco-reembolso="c1"/)
  const iBloco = h.indexOf('data-bloco-reembolso')
  assert.ok(h.indexOf('Valor pago pelo comprador') < iBloco, 'abaixo do valor pago')
  assert.ok(h.indexOf('Tempo de pagamento') < iBloco, 'depois dos valores (o quadro não se parte)')
  assert.ok(iBloco < h.indexOf('Nota fiscal'), 'antes da NF')
  assert.ok(iBloco < h.indexOf('Informações logísticas'))
  assert.doesNotMatch(h, /data-dialogo-falso/, 'o diálogo só com o clique')

  // Sem retrato (TikTok): depois do quadro do pedido, sem sumir.
  m = montarPainel({ ...base, conversa: conversa({ plataforma: 'tiktok', pedido_marketplace: '5770001' }), pedidoMkt: null }, { filhos })
  h = await m.html()
  assert.equal((h.match(/data-bloco-reembolso=/g) || []).length, 1, 'o bloco existe sem retrato')
  assert.ok(h.indexOf('ainda não vem pela API') < h.indexOf('data-bloco-reembolso'), 'depois do quadro do pedido')
  assert.ok(h.indexOf('5770001') < h.indexOf('data-bloco-reembolso'))

  // Sem pedido (pré-venda) e no Direct do Instagram: nada de bloco.
  m = montarPainel({ ...base, conversa: conversa({ plataforma: 'ml', canal: 'pergunta', pedido_marketplace: null }), pedidoMkt: null }, { filhos })
  m.estado.aba = 'pedido'
  assert.doesNotMatch(await m.html(), /data-bloco-reembolso/)
  m = montarPainel({ ...base, conversa: conversa({ id: 'ig:1', plataforma: 'instagram', pedido_marketplace: null }), pedidoMkt: null }, { filhos })
  assert.doesNotMatch(await m.html(), /data-bloco-reembolso/)
  // O pedido só no painel (o nº do Bling): o bloco aparece.
  m = montarPainel({ ...base, conversa: conversa({ plataforma: 'amazon', pedido_marketplace: null }), pedidoMkt: null, painel: { pedido: { numero_bling: '300101' }, estoque: { itens: [] }, links: {} } }, { filhos })
  m.estado.aba = 'pedido'
  assert.match(await m.html(), /data-bloco-reembolso/)

  // O "Lançar" do bloco abre o diálogo, montado fora dos ramos; lançou → o bloco relê.
  m = montarPainel({ ...base, conversa: conversa(), pedidoMkt: retrato('251117ABC') }, { filhos })
  m.estado.abrirReembolso(resposta())
  assert.equal(m.estado.reembolsoDialogo, true)
  h = await m.html()
  assert.match(h, /data-dialogo-falso="c1" data-aberto="true"/)
  assert.match(h, /data-bloco-reembolso="c1" data-sinal="0"/)
  m.estado.reembolsoSinal += 1
  assert.match(await m.html(), /data-sinal="1"/)

  // O pedido da conversa mudou com o diálogo aberto: fecha (o lançamento cairia no outro).
  m.props.conversa = conversa({ pedido_marketplace: '251117XYZ' })
  await esperar()
  assert.equal(m.estado.reembolsoDialogo, false)
  assert.deepEqual(m.avisos, [['O pedido da conversa mudou', 'Abra o lançamento do reembolso de novo, já no pedido certo.']])
  // Fechado, a mudança de pedido não avisa nada.
  m.props.conversa = conversa({ pedido_marketplace: '251117ABC' })
  await esperar()
  assert.equal(m.avisos.length, 1)

  // O painel relido (a cada 2 min, o "atualizar"): o bloco relê junto; a 1ª leitura não.
  m = montarPainel({ ...base, conversa: conversa(), pedidoMkt: retrato('251117ABC') }, { filhos })
  m.props.painel = { pedido: null, estoque: { itens: [] }, links: {} }
  await esperar()
  assert.equal(m.estado.reembolsoSinal, 0, 'a primeira leitura do painel não dobra o GET')
  m.props.painel = { pedido: null, estoque: { itens: [] }, links: {} }
  await esperar()
  assert.equal(m.estado.reembolsoSinal, 1)

  // O atalho: volta para a aba Pedido, rola até o bloco e marca por uns segundos.
  const timers = { fila: [], setTimeout(fn, ms) { this.fila.push([fn, ms]); return this.fila.length }, clearTimeout() {} }
  timers.setTimeout = timers.setTimeout.bind(timers)
  m = montarPainel({ ...base, conversa: conversa(), pedidoMkt: retrato('251117ABC') }, { filhos, timers })
  assert.equal(typeof m.exposto.mostrarReembolso, 'function', 'exposto para a conversa')
  assert.equal(typeof m.exposto.mostrarDevolucao, 'function', 'o da devolução continua')
  m.estado.aba = 'produto'
  const rolados = []
  m.estado.rolagemPainel = {
    querySelector: (sel) => {
      assert.equal(sel, '[data-reembolso-cliente]')
      return { scrollIntoView: (o) => rolados.push(o.block) }
    },
  }
  await m.estado.mostrarReembolso()
  assert.equal(m.estado.aba, 'pedido')
  assert.deepEqual(rolados, ['center'])
  assert.equal(m.estado.reembolsoDestacado, true)
  assert.match(await m.html(), /data-destaque="true"/)
  assert.equal(timers.fila[0][1], 2500)
  timers.fila[0][0]()
  assert.equal(m.estado.reembolsoDestacado, false, 'o destaque sai sozinho')
  // Sem o bloco (a conversa não tem pedido): avisa em vez de rolar para o nada.
  m.estado.rolagemPainel = { querySelector: () => null }
  await m.estado.mostrarReembolso()
  assert.deepEqual(m.avisos.at(-1), ['Esta conversa não tem pedido ligado', 'O reembolso ao cliente é lançado pelo pedido da conversa.'])
  m.estado.rolagemPainel = null
  await m.estado.mostrarReembolso()

  // No fonte: os dois encaixes e o diálogo fora dos ramos, como o da troca.
  const painel = sfc(REL_PEDIDO).descriptor.template.content
  const a = painel.indexOf('<AtendimentoReembolsoCliente')
  const b = painel.indexOf('<AtendimentoReembolsoCliente', a + 1)
  assert.ok(a > painel.indexOf('Valor pago pelo comprador') && a > painel.indexOf('Tempo de pagamento') && a < painel.indexOf('<!-- nota fiscal -->'), 'o 1º logo abaixo dos valores')
  assert.ok(b > painel.indexOf('{{ semRetrato }}') && b < painel.indexOf('<!-- painel do pedido: carregando / falhou -->'), 'o 2º no ramo sem retrato')
  assert.equal(painel.indexOf('<AtendimentoReembolsoCliente', b + 1), -1, 'só os dois encaixes')
  const d = painel.indexOf('<AtendimentoReembolsoLancar')
  assert.ok(d > painel.indexOf('<AtendimentoTroca'), 'o diálogo mora junto do da troca, fora dos ramos')
  assert.match(painel, /<AtendimentoReembolsoCliente\s+v-if="temReembolso"\s+:key="chaveReembolso"\s+:conversa-id="conversa\.id"\s+:sinal="reembolsoSinal"\s+:destaque="reembolsoDestacado"\s+@lancar="abrirReembolso"/)
  assert.match(painel, /@lancado="reembolsoSinal \+= 1"/)
  assert.match(sfc(REL_PEDIDO).descriptor.scriptSetup.content, /defineExpose\(\{ mostrarDevolucao, mostrarReembolso \}\)/)
}

// ================================================ a conversa: o atalho na faixa
async function testarConversa() {
  const conv = sfc('../components/AtendimentoConversa.vue').descriptor
  const setup = conv.scriptSetup.content
  const tpl = conv.template.content
  assert.match(setup, /^import \{ pedeReembolso \} from '~\/components\/AtendimentoReembolsoCliente\.vue'$/m)
  // Na faixa da Caixa Humano, depois dos motivos (aberta ou recolhida).
  const faixa = (tpl.match(/<div\n {10}v-if="conversa\.humano"[\s\S]*?\n {8}<\/div>/) || [])[0]
  assert.ok(faixa, 'a faixa da Caixa Humano')
  assert.match(faixa, /<button\s+v-if="atalhoReembolso"[\s\S]*?data-atalho-reembolso\s+@click="verReembolsoNoPedido"\s*>\s*<HandCoins[^>]*\/> lançar reembolso\s*<\/button>/)
  assert.ok(faixa.indexOf('data-faixa-humano-resumo') < faixa.indexOf('data-atalho-reembolso'), 'depois dos motivos')

  // A faixa renderizada: com o atalho, o botão; sem, nada.
  const renderFaixa = async (ctx) => {
    const { code, errors } = compileTemplate({ source: `<div>${faixa}</div>`, filename: 'faixa.vue', id: 'faixa', compilerOptions: { isTS: true } })
    assert.deepEqual(errors, [])
    const tmod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(code))(requerer, tmod, tmod.exports)
    const app = Vue.createSSRApp({ setup: () => ctx, render: tmod.exports.render })
    app.config.warnHandler = () => {}
    for (const n of ['ChevronDown', 'ChevronRight', 'UserRound', 'HandCoins']) app.component(n, icone(n))
    return semComentarios(await renderToString(app))
  }
  const humano = { motivos: ['acao_pessoa'], rotulos: ['Ação de pessoa'], assuntos: ['aprovar_fora_da_plataforma'], assuntos_rotulos: ['Aprovar fora da plataforma'], na_caixa: true }
  const cliques = []
  const ctx = (atalho) => ({
    conversa: { humano }, corHumano: P.corHumano, faixaHumano: P.faixaHumano, rotulosHumano: P.rotulosHumano, resumoHumano: P.resumoHumano,
    tituloForaDaCaixaHumano: 'fora', caixaHumanoAberta: true, alternarCaixaHumano: () => {}, atalhoReembolso: atalho, verReembolsoNoPedido: () => cliques.push(1),
  })
  let h = await renderFaixa(ctx(true))
  assert.match(h, /data-atalho-reembolso[^>]*>[\s\S]*?data-icone="HandCoins"[\s\S]*?lançar reembolso\s*<\/button>/)
  assert.match(h, /title="abre a aba Pedido no bloco Reembolso ao cliente/)
  h = await renderFaixa(ctx(false))
  assert.doesNotMatch(h, /data-atalho-reembolso/)

  // O setup de verdade do atalho (o trecho), com o painel falso.
  const js = trecho(setup, '// O ATALHO DO REEMBOLSO', '// O cartão do topo, aberto (padrão) ou recolhido.')
    + '\nreturn { atalhoReembolso, verReembolsoNoPedido }'
  const conversa = Vue.ref(null)
  const semPainelDePedido = Vue.ref(false)
  const pedidoVisivel = Vue.ref(false)
  const passos = []
  const alternarPedido = () => { passos.push('abrir'); pedidoVisivel.value = true }
  const painelPedidoRef = Vue.ref({ mostrarReembolso: async () => { passos.push('rolar') } })
  const x = new Function('computed', 'conversa', 'semPainelDePedido', 'pedeReembolso', 'pedidoVisivel', 'alternarPedido', 'nextTick', 'painelPedidoRef', js)(
    Vue.computed, conversa, semPainelDePedido, RC.pedeReembolso, pedidoVisivel, alternarPedido, Vue.nextTick, painelPedidoRef,
  )
  assert.equal(x.atalhoReembolso.value, false, 'sem conversa aberta')
  conversa.value = { id: 'c1', humano }
  assert.equal(x.atalhoReembolso.value, true, 'Aprovar fora da plataforma')
  conversa.value = { id: 'c1', humano: { ...humano, motivos: ['assunto'], assuntos: ['reembolso'] } }
  assert.equal(x.atalhoReembolso.value, true, 'o assunto reembolso')
  conversa.value = { id: 'c1', humano: { ...humano, assuntos: ['analisar_evidencia'] } }
  assert.equal(x.atalhoReembolso.value, false, 'outra ação de pessoa')
  conversa.value = { id: 'c1', humano: null }
  assert.equal(x.atalhoReembolso.value, false, 'fora da Caixa Humano')
  conversa.value = { id: 'ig:9', humano }
  assert.equal(x.atalhoReembolso.value, false, 'o Direct não tem pedido')
  conversa.value = { id: 'c1', humano }
  semPainelDePedido.value = true
  assert.equal(x.atalhoReembolso.value, false, 'carrinho, comentário e Zap não têm o painel')
  semPainelDePedido.value = false
  // O clique: abre o painel (se fechado) e rola; aberto, só rola.
  await x.verReembolsoNoPedido()
  assert.deepEqual(passos, ['abrir', 'rolar'])
  await x.verReembolsoNoPedido()
  assert.deepEqual(passos, ['abrir', 'rolar', 'rolar'])
  // O ref do painel declara os dois caminhos.
  assert.match(setup, /const painelPedidoRef = ref<\{ mostrarDevolucao: \(id: string\) => Promise<void>; mostrarReembolso: \(\) => Promise<void> \} \| null>\(null\)/)
}

principal().then(
  () => console.log('ok: atendimento-reembolso-cliente (ajudantes, bloco, diálogo, painel e o atalho da faixa)'),
  (e) => { console.error(e); process.exit(1) },
)
