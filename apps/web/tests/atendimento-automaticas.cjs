// node tests/atendimento-automaticas.cjs — a aba "Automáticas" do /atendimento (05/10/2026).
// Eduardo: recriar no DaVinci as mensagens automáticas que o Duoke manda hoje,
// COMEÇANDO EM MODO SECO (registra o que mandaria e compara com o Duoke; nada
// sai). Desenho em docs/atendimento-automacoes.md (§8.2). Aqui, a tela:
//  - o contrato com o backend (routers/atendimento_automacoes.py): as rotas,
//    os campos de cada saída (campo a campo, nos dois sentidos), o corpo do
//    PATCH, os códigos de erro e de "por que não enviar", os estados, o Duoke,
//    os gatilhos, as condições e os filtros do registro;
//  - as regras puras (a % que bateu, verde a partir de 95%; o "Enviar"
//    desabilitado com o motivo; o corpo do PATCH só com o que mudou; o que
//    segura o salvar; a comparação de cada linha do registro);
//  - a tela com a API falsa: o que ela chama (e o que NUNCA chama: nada de
//    envio), o modo, a confirmação "desliguei no Duoke", o editor com a
//    prévia, o registro e seus filtros; e a tela renderizada (Vue SSR):
//    carregando, erro, vazio, a faixa, o "Enviar" travado, nenhum texto de
//    comprador no registro, tema escuro e tela estreita;
//  - a página (a aba entre "Respostas prontas" e "Métricas") e a conversa (o
//    rótulo da mensagem automática do DaVinci).
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
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}
const cache = new Map()
function requerer(nome) {
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')

const autSfc = sfc('../components/AtendimentoAutomaticas.vue')
const A = exportsDe(autSfc.descriptor)
const P = requerer('~/components/AtendimentoPlataforma.vue')
const setupSrc = autSfc.descriptor.scriptSetup.content
const tela = autSfc.descriptor.template.content

// ------------------------------------------------ helpers do contrato
const rota = api('routers/atendimento_automacoes.py')
const catalogo = api('services/atendimento/automacoes_catalogo.py')
const comparar = api('services/atendimento/automacoes_comparar.py')
const mainPy = api('main.py')
const constantes = api('services/atendimento/constantes.py')

// O corpo de uma função Python (até a próxima definição de topo).
function corpoDef(fonte, nome) {
  const i = fonte.search(new RegExp(`^(?:async )?def ${nome}\\(`, 'm'))
  assert.ok(i >= 0, `def ${nome}`)
  const resto = fonte.slice(i + 1)
  const fim = resto.search(/\n(?:@router|def |async def |class |[A-Z_]+(?:: [^=\n]+)? = )/)
  return fim >= 0 ? resto.slice(0, fim) : resto
}
// O miolo do primeiro `{ … }` depois do marcador (com as chaves aninhadas).
function dictApos(fonte, marcador) {
  const i = fonte.indexOf(marcador)
  assert.ok(i >= 0, `marcador ${marcador}`)
  const ini = fonte.indexOf('{', i + marcador.length - 1)
  let prof = 0
  for (let j = ini; j < fonte.length; j++) {
    if (fonte[j] === '{') prof++
    else if (fonte[j] === '}' && --prof === 0) return fonte.slice(ini + 1, j)
  }
  throw new Error(`dict sem fim: ${marcador}`)
}
// As chaves do NÍVEL DE CIMA de um dict literal.
function chavesTopo(miolo) {
  let t = miolo
  for (let k = 0; k < 20; k++) {
    const antes = t
    t = t.replace(/\([^()]*\)/g, '()').replace(/\[[^[\]]*\]/g, '[]').replace(/\{[^{}]*\}/g, '{}')
    if (t === antes) break
  }
  return [...new Set([...t.matchAll(/"([a-z_0-9]+)":/g)].map((m) => m[1]))]
}
const camposPy = (classe) => {
  const m = rota.match(new RegExp(`^class ${classe}\\(BaseModel\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) )`, 'm'))
  assert.ok(m, classe)
  return [...m[1].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
}
const camposTs = (nome) => {
  const m = autSfc.fonte.match(new RegExp(`export interface ${nome} \\{\\n([\\s\\S]*?)\\n\\}`))
  assert.ok(m, nome)
  return [...m[1].matchAll(/^ {2}([a-z_0-9]+)\??: /gm)].map((x) => x[1])
}
const igual = (a, b, msg) => assert.deepEqual([...a].sort(), [...b].sort(), msg)
const constPy = (fonte, prefixo) => Object.fromEntries([...fonte.matchAll(new RegExp(`^(${prefixo}[A-Z_]+) = "([a-z_]+)"`, 'gm'))].map((m) => [m[1], m[2]]))

// ------------------------------------------------ o contrato com o backend
{
  // As rotas, com a trava do /atendimento, registradas no app.
  assert.match(rota, /prefix="\/api\/atendimento", tags=\["atendimento"\], dependencies=\[Depends\(_so_admin\)\]/)
  assert.match(mainPy, /atendimento_automacoes/)
  const rotas = [...rota.matchAll(/^@router\.(get|post|patch|delete|put)\("([^"]+)"/gm)].map((m) => `${m[1].toUpperCase()} ${m[2]}`)
  igual(rotas, [
    'GET /automacoes',
    'GET /automacoes/registro',
    'GET /automacoes/estatisticas',
    'PATCH /automacoes/{automacao}/{integration_id}',
    'POST /automacoes/{automacao}/simular-nas-lojas-do-duoke',
    'POST /automacoes/previa',
  ], 'as rotas da aba')
  // A tela chama exatamente estas (e nenhuma rota de envio).
  assert.ok(setupSrc.includes('`/api/atendimento/automacoes?plataforma=${encodeURIComponent(plataforma.value)}&dias=${dias.value}`'))
  assert.ok(setupSrc.includes('`/api/atendimento/automacoes/registro?${paramsRegistro(filtrosRegistro)}`'))
  assert.ok(setupSrc.includes('`/api/atendimento/automacoes/registro?${paramsRegistro(filtrosRegistro, registroProximo.value)}`'))
  assert.equal((setupSrc.match(/`\/api\/atendimento\/automacoes\/\$\{encodeURIComponent\(aut\.codigo\)\}\/\$\{encodeURIComponent\(loja\.integration_id\)\}`, \{\n\s+method: 'PATCH',/g) || []).length, 2, 'modo e regra pelo PATCH')
  assert.ok(setupSrc.includes("`/api/atendimento/automacoes/${encodeURIComponent(aut.codigo)}/simular-nas-lojas-do-duoke`, { method: 'POST' }"))
  assert.ok(setupSrc.includes("'/api/atendimento/automacoes/previa', {\n      method: 'POST',"))
  for (const proibida of ['/responder', '/conversas/', '/mensagens/', '/conferir', 'send_', 'auto_reply_message']) {
    assert.ok(!autSfc.fonte.includes(proibida), `a aba não fala com envio (${proibida})`)
  }
  const urls = [...autSfc.fonte.matchAll(/['`](\/api\/[^'`$?]*)/g)].map((m) => m[1])
  assert.ok(urls.length >= 5 && urls.every((u) => u.startsWith('/api/atendimento/automacoes')), `só as rotas da aba: ${urls}`)

  // Os campos: os da tela = os do backend (nos dois sentidos).
  igual(camposTs('ChavesAutomacao'), chavesTopo(dictApos(corpoDef(rota, 'chaves'), 'return {')), 'chaves()')
  igual(camposTs('FaixaAutomacao'), ['nivel', 'texto'])
  assert.deepEqual([...corpoDef(rota, '_faixa').matchAll(/"nivel": "([a-z]+)"/g)].map((m) => m[1]).sort(), ['aviso', 'aviso', 'erro', 'info'])
  igual(camposTs('RegraAutomacao'), chavesStr(corpoDef(rota, '_regra_out')), '_regra_out (com e sem regra salva)')
  const regraOut = corpoDef(rota, '_regra_out')
  igual(chavesTopo(dictApos(regraOut, 'return {')), chavesTopo(dictApos(regraOut.slice(regraOut.indexOf('return {') + 8), 'return {')), 'as duas saídas da regra têm os mesmos campos')
  const listar = corpoDef(rota, 'listar_automacoes')
  igual(camposTs('LojaAutomacao'), chavesTopo(dictApos(listar, 'linhas_lojas.append(')), 'a loja da lista')
  igual(camposTs('Automacao'), [...chavesTopo(dictApos(corpoDef(rota, '_aut_out'), 'return {')), ...chavesTopo(dictApos(listar, 'saida.append('))], '_aut_out + totais + lojas')
  igual(camposTs('AutomacoesResposta'), chavesTopo(dictApos(listar, 'return {')), 'GET /automacoes')
  igual(camposTs('RegistroLinha'), chavesTopo(dictApos(corpoDef(rota, '_linha_out'), 'return {')), '_linha_out')
  igual(camposTs('RegistroResposta'), chavesTopo(dictApos(corpoDef(rota, 'registro'), 'return {')), 'GET /registro')
  igual(camposTs('RegraMudou'), chavesTopo(dictApos(corpoDef(rota, 'mudar_regra'), 'return {')), 'PATCH')
  igual(camposTs('SimularNasLojas'), chavesTopo(dictApos(corpoDef(rota, 'simular_nas_lojas_do_duoke'), 'return {')), 'simular nas lojas do Duoke')
  igual(camposTs('PreviaResposta'), chavesTopo(dictApos(corpoDef(rota, 'previa'), 'return {')), 'prévia')
  igual(camposTs('CorpoRegra'), camposPy('RegraIn'), 'o corpo do PATCH = RegraIn')
  igual(camposTs('ParteAutomacao'), camposPy('ParteIn'), 'a parte = ParteIn')
  // A CONTA: as colunas do comparador + a mediana + os motivos + o resumo.
  const est = corpoDef(comparar, 'estatisticas')
  const conta = [
    ...chavesTopo(dictApos(est, 'colunas = {')),
    'diferenca_mediana_s',
    'atraso_mediana_s',
    'motivos',
    ...chavesTopo(dictApos(corpoDef(comparar, 'resumir'), 'return {')),
  ]
  assert.match(est, /contagem\["diferenca_mediana_s"\] = /)
  assert.match(est, /contagem\["atraso_mediana_s"\] = /)
  assert.match(est, /contagem\["motivos"\] = /)
  assert.match(est, /contagem\["_diferencas"\] = lista/, 'a interna (fora da rota: sem_internos)')
  assert.match(est, /contagem\["_atrasos"\] = lista_atraso/, 'a interna (fora da rota: sem_internos)')
  assert.match(corpoDef(comparar, 'somar'), /saida\["atraso_mediana_s"\] = /, 'a soma das lojas refaz a mediana do atraso')
  // O critério da troca da loja é sempre de 7 dias (o período da tela muda a conta, não ele).
  assert.match(rota, /^CRITERIO_DIAS = 7$/m)
  assert.match(listar, /criterio = e7\.get\(\(aut\.codigo, integ\.id\)\) or comparar\.resumir\(\{\}, aut\)/)
  igual(camposTs('ContaAutomacao'), conta, 'a CONTA')
  assert.match(corpoDef(comparar, 'sem_internos'), /if not k\.startswith\("_"\)/)

  // O registro não tem texto: os únicos "*_texto" são as traduções de código.
  const linha = chavesTopo(dictApos(corpoDef(rota, '_linha_out'), 'return {'))
  igual(linha.filter((k) => /texto|comprador|nome|mensagem$/.test(k)), ['motivo_texto', 'divergencia_texto', 'alerta_texto', 'automacao_nome'])
  assert.match(corpoDef(rota, '_linha_out'), /"automacao_nome": aut\.nome if aut else x\.automacao/, 'o nome é o da AUTOMAÇÃO')

  // Constantes e limites.
  assert.equal(A.NOME_EXEMPLO, (rota.match(/^NOME_EXEMPLO = "([^"]+)"$/m) || [])[1])
  const limite = Number((rota.match(/^LIMITE_REGISTRO = (\d+)$/m) || [])[1])
  assert.ok(A.LIMITE_REGISTRO > 0 && A.LIMITE_REGISTRO <= limite)
  assert.match(corpoDef(rota, 'registro'), /limite: Annotated\[int, Query\(ge=1, le=LIMITE_REGISTRO\)\] = 200/)
  assert.match(listar, /plataforma: Annotated\[str \| None, Query\(pattern="\^\(shopee\|tiktok\|ml\)\$"\)\] = None/)
  igual(A.PLATAFORMAS_AUTOMACAO.map((p) => p.value), ['shopee', 'tiktok', 'ml'])
  const diasMax = Number((listar.match(/dias: Annotated\[int, Query\(ge=1, le=(\d+)\)\]/) || [])[1])
  assert.ok(Math.max(...A.PERIODOS) <= diasMax && Math.min(...A.PERIODOS) >= 1, 'os períodos cabem na API')
  for (const p of ['automacao', 'integration_id', 'estado', 'duoke', 'so', 'limite', 'antes']) {
    assert.match(corpoDef(rota, 'registro'), new RegExp(`^ {4}${p}: `, 'm'), `o registro aceita ${p}`)
  }
  assert.match(rota, /atraso_min: int \| None = Field\(default=None, ge=0, le=10080\)/)
  assert.match(rota, /teto_dia: int \| None = Field\(default=None, ge=0, le=6000\)/)
  assert.match(rota, /partes: list\[ParteIn\] \| None = Field\(default=None, max_length=5\)/)
  assert.match(rota, /texto: str \| None = Field\(default=None, max_length=2000\)/)

  // O vocabulário do catálogo: modos, estados, Duoke, gatilhos, alvos, condições.
  igual(A.MODOS_AUTOMACAO.map((m) => m.value), Object.values(constPy(catalogo, 'MODO_')), 'modos')
  igual(Object.keys(A.ESTADOS_REGISTRO), Object.values(constPy(catalogo, 'ESTADO_')), 'estados')
  igual(Object.keys(A.DUOKE_REGISTRO), Object.values(constPy(catalogo, 'DUOKE_')), 'o que o Duoke fez')
  igual(Object.keys(A.GATILHOS), Object.values(constPy(catalogo, 'GATILHO_')), 'gatilhos')
  for (const a of Object.values(constPy(catalogo, 'ALVO_'))) assert.ok(A.ALVOS[a], `alvo ${a}`)
  assert.ok(A.ALVOS.mensagem, 'o alvo "mensagem" do model')
  const condicoes = new Set()
  for (const m of catalogo.matchAll(/condicoes=\{([^}]*)\}/g)) for (const k of m[1].matchAll(/"([a-z_]+)":/g)) condicoes.add(k[1])
  igual(Object.keys(A.CONDICOES), [...condicoes], 'as condições do catálogo têm nome na tela')
  const combinadas = new Set([...catalogo.matchAll(/diferenca_combinada="([a-z_]+)"/g)].map((m) => m[1]))
  for (const c of combinadas) assert.ok(A.SELOS_COMBINADA[c], `selo ${c}`)
  assert.match(catalogo, /^PLACEHOLDERS: dict\[str, str\] = \{\n {4}"comprador": /m)

  // Por que não enviar (a lista da API e o 409) e os códigos de erro.
  const porQue = [...corpoDef(rota, '_por_que_nao_enviar').matchAll(/motivos\.append\("([a-z_]+)"\)/g)].map((m) => m[1])
  igual(Object.keys(A.POR_QUE_NAO_ENVIAR), porQue, 'por que não enviar')
  const codigos = new Set([...rota.matchAll(/"code": "([a-z_]+)"/g)].map((m) => m[1]))
  assert.ok(codigos.size >= 8)
  for (const c of codigos) assert.ok(A.ERROS_AUTOMACAO[c], `erro ${c} tem frase`)
  assert.match(corpoDef(rota, 'mudar_regra'), /"code": motivos\[0\],\n\s+"motivos": motivos,/, 'o 409 traz a lista toda')

  // O filtro "comparação" = os `so` do registro (+ o Duoke pendente).
  const so = Object.keys(Object.fromEntries([...dictApos(rota, '_FILTROS_SO = {').matchAll(/^ {4}"([a-z_]+)":/gm)].map((m) => [m[1], 1])))
  igual(A.FILTROS_COMPARACAO.map((f) => f.value).filter((v) => v && !v.startsWith('duoke:')), so)
  for (const f of A.FILTROS_COMPARACAO.filter((x) => x.value.startsWith('duoke:'))) {
    assert.ok(Object.values(constPy(catalogo, 'DUOKE_')).includes(f.value.slice(6)))
  }
  assert.match(corpoDef(rota, 'registro'), /Query\(pattern="\^\(so_davinci\|so_duoke\|bateu\|alerta\|combinada\)\$"\)/)
  // A comparação da linha = a conta do comparador (os estados que "mandam").
  assert.match(comparar, /_ESTADOS_QUE_MANDAM = \(\n {4}cat\.ESTADO_SIMULADO,\n {4}cat\.ESTADO_ENVIADO,\n {4}cat\.ESTADO_ENVIANDO,\n {4}cat\.ESTADO_REVISAR,\n\)/)
  assert.match(autSfc.fonte, /const ESTADOS_QUE_MANDAM = \['simulado', 'enviado', 'enviando', 'revisar'\]/)
  assert.match(comparar, /^CRITERIO_MINIMO = 0\.95$/m, 'verde a partir de 95% = o critério da troca')

  // A origem da mensagem automática (a conversa ganha o rótulo).
  assert.match(constantes, /^ORIGEM_AUTO = "davinci_auto"$/m)
  assert.match(constantes, /^ORIGENS_DAVINCI = \(ORIGEM_HUMANO, ORIGEM_IA, ORIGEM_AUTO\)$/m)
}
function chavesStr(txt) {
  return [...new Set([...txt.matchAll(/"([a-z_0-9]+)":/g)].map((m) => m[1]))]
}

// ------------------------------------------------ dados de teste (= os tipos)
const ISO = '2026-10-05T12:00:00+00:00'
const AGORA = Date.parse('2026-10-05T12:00:00Z')
const MENU = 'Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n1 - Previsão de entrega / envio'
const conta = (o = {}) => ({
  total: 0, simulado: 0, enviado: 0, pulado: 0, falhou: 0, agendado: 0, pendente: 0, bateu_mandou: 0, bateu_nao_mandou: 0,
  so_davinci: 0, so_davinci_2d: 0, so_duoke: 0, combinada: 0, alertas: 0, texto_invalido: 0, envio_desligado: 0,
  diferenca_mediana_s: null, atraso_mediana_s: null, motivos: {}, casos: 0, precisao: null, cobertura: null, concordancia: null, pode_trocar: false, por_que_nao: [], ...o,
})
const regra = (o = {}) => ({
  id: 'r-1', modo: 'simular', padrao: false, partes: [{ tipo: 'texto', texto: MENU }], atraso_min: 1, janela_inicio: null, janela_fim: null,
  condicoes: { sessao_h: 12, pular_em_disputa_com_pessoa: true }, teto_dia: null, versao: 1, ligada_desde: ISO, enviar_desde: null,
  disjuntor_em: null, disjuntor_motivo: null, atualizado_em: ISO, ...o,
})
const TRAVADO = ['envio_desligado', 'envio_geral_desligado']
const loja = (o = {}) => ({
  integration_id: 'i-barbosa', loja: 'Barbosa', integracao: 'barbosa', canal_status: 'ok', canal_modo: 'observar', sem_acesso: false,
  duoke_hoje: true, regra: regra(), h24: conta(), periodo: conta(), ultimo: null, pode_enviar: false, por_que_nao_enviar: [...TRAVADO],
  pode_trocar: false, por_que_nao_trocar: ['poucos casos (0 de 30)'], ...o,
})
const PLACEHOLDERS = { comprador: 'o usuário do comprador na plataforma (some se não houver)' }
const aut = (o = {}) => ({
  codigo: 'shopee_menu', plataforma: 'shopee', canal: 'chat', nome: 'Menu "selecione sua dúvida"', descricao: 'Mensagem do comprador sem robô nas últimas 12 h → 1 min.',
  tipo: 'menu', gatilho: 'mensagem', alvo: 'conversa', familia: 'conversa', campanha: false, travada: false, diferenca_combinada: null,
  atraso_min: 1, validade_min: 30, janela_inicio: null, janela_fim: null, condicoes_padrao: { sessao_h: 12, pular_em_disputa_com_pessoa: true },
  placeholders: { ...PLACEHOLDERS }, seguinte: null, total_24h: null, total_periodo: null, lojas: [loja()], ...o,
})
const CHAVES = { leitura_ativa: true, motor_ativo: true, envio_automacoes: false, envio_geral: false, shopee_auto_reply: false, shopee_auto_reply_adaptador: false, shopee_mensagens_comprador: true, teto_dia: 400 }
const FAIXA_SECO = 'Modo seco: o DaVinci registra o que mandaria e compara com o Duoke. Nada é enviado (ATENDIMENTO_AUTOMACOES_ENVIO desligada).'
const resposta = (o = {}) => ({
  chaves: { ...CHAVES }, faixa: { nivel: 'info', texto: FAIXA_SECO }, dias: 7,
  motivos: { pessoa_respondeu: 'Alguém da equipe já respondeu', ja_mandado: 'Já mandado dentro do intervalo' },
  divergencias: { horario_comercial: 'Entregue: o DaVinci manda das 9h às 20h; o Duoke de madrugada' },
  alertas: { devolucao: 'Mandaria para quem devolveu ou pediu reembolso' },
  automacoes: [], ...o,
})
const linhaReg = (o = {}) => ({
  id: 'l-1', automacao: 'shopee_menu', automacao_nome: 'Menu "selecione sua dúvida"', plataforma: 'shopee', integration_id: 'i-barbosa', loja: 'Barbosa',
  alvo: 'conversa', pedido: null, conversa_id: 'c-1', evento_em: ISO, visto_em: ISO, devido_em: ISO, decidido_em: ISO, estado: 'simulado', modo: 'simular',
  motivo: null, motivo_texto: null, erro: null, duoke: 'mandou', duoke_em: ISO, duoke_diferenca_s: 240, divergencia: null, divergencia_texto: null,
  alerta: null, alerta_texto: null, tentativas: 0, regra_versao: 1, ...o,
}); // sem o `;`, o parser do TypeScript (o typecheck do Nuxt) lê o bloco abaixo como corpo de arrow
{
  // Os dados de teste têm exatamente os campos dos tipos (= os do backend).
  igual(Object.keys(conta()), camposTs('ContaAutomacao'))
  igual(Object.keys(regra()), camposTs('RegraAutomacao'))
  igual(Object.keys(loja()), camposTs('LojaAutomacao'))
  igual(Object.keys(aut()), camposTs('Automacao'))
  igual(Object.keys(resposta()), camposTs('AutomacoesResposta'))
  igual(Object.keys(linhaReg()), camposTs('RegistroLinha'))
  igual(Object.keys(CHAVES), camposTs('ChavesAutomacao'))
}

// ------------------------------------------------ regras puras
{
  // A %: para BAIXO (94,96% não aparece como "95%" verde), em pt-BR.
  assert.equal(A.fmtPct(0.987), '98,7%')
  assert.equal(A.fmtPct(1), '100%')
  assert.equal(A.fmtPct(0.95), '95%')
  assert.equal(A.fmtPct(0.94996), '94,9%')
  assert.equal(A.fmtPct(0), '0%')
  assert.equal(A.fmtPct(null), '—')
  assert.equal(A.fmtPct(undefined), '—')
  // Verde a partir de 95%.
  assert.equal(A.nivelPct(0.95), 'bom')
  assert.equal(A.nivelPct(0.9499), 'quase')
  assert.equal(A.nivelPct(0.85), 'quase')
  assert.equal(A.nivelPct(0.6), 'ruim')
  assert.equal(A.nivelPct(null), null)
  assert.match(A.clsPct(0.988), /text-emerald-700 dark:text-emerald-300/)
  assert.match(A.clsPct(0.9), /text-amber-700 dark:text-amber-300/)
  assert.match(A.clsPct(0.5), /text-red-600 dark:text-red-400/)
  assert.equal(A.clsPct(null), 'text-muted-foreground')

  // A "% que bateu": a MENOR entre precisão e cobertura (a concordância soma
  // "nenhum dos dois mandou" e pintava de verde uma cobertura de 94%); nas
  // opções, a cobertura.
  const c = conta({ concordancia: 0.957, cobertura: 0.944, precisao: 0.99, casos: 40 })
  assert.deepEqual(A.bateuDe(c, 'menu'), { valor: 0.944, medida: 'cobertura', casos: 40 })
  assert.equal(A.nivelPct(A.bateuDe(c, 'menu').valor), 'quase', 'concordância 95,7% não pinta de verde a cobertura de 94,4%')
  assert.deepEqual(A.bateuDe({ ...c, precisao: 0.8, cobertura: 0.97 }, 'menu'), { valor: 0.8, medida: 'precisão', casos: 40 })
  assert.deepEqual(A.bateuDe({ ...c, precisao: null, cobertura: 0 }, 'menu'), { valor: 0, medida: 'cobertura', casos: 40 })
  assert.deepEqual(A.bateuDe({ ...c, concordancia: null, precisao: null, cobertura: 0.97 }, 'opcao'), { valor: 0.97, medida: 'cobertura', casos: 40 })
  assert.deepEqual(A.bateuDe({ ...c, precisao: 0.5, cobertura: 0.97 }, 'opcao'), { valor: 0.97, medida: 'cobertura', casos: 40 }, 'nas opções a precisão nunca entra')
  assert.deepEqual(A.bateuDe(null, 'opcao'), { valor: null, medida: 'cobertura', casos: 0 })
  assert.deepEqual(A.bateuDe(null, 'menu'), { valor: null, medida: 'cobertura', casos: 0 })
  assert.equal(A.mandaria(conta({ simulado: 7, enviado: 2 })), 9)
  assert.equal(A.mandaria(null), 0)

  // Horários e atrasos em linguagem de gente.
  assert.equal(A.fmtDiferenca(30), 'Duoke na mesma hora')
  assert.equal(A.fmtDiferenca(240), 'Duoke 4 min depois')
  assert.equal(A.fmtDiferenca(-7200), 'Duoke 2 h antes')
  assert.equal(A.fmtDiferenca(null), '')
  assert.equal(A.fmtDiferenca(-7200, false), '2 h antes')
  assert.equal(A.fmtDiferenca(10, false), 'na mesma hora')
  assert.equal(A.fmtAtrasoReal(30), 'menos de 1 min')
  assert.equal(A.fmtAtrasoReal(180), '3 min')
  assert.equal(A.fmtAtrasoReal(null), '')
  assert.equal(A.fmtAtraso(0), 'na hora')
  assert.equal(A.fmtAtraso(10), '10 min')
  assert.equal(A.fmtAtraso(240), '4 h')
  assert.equal(A.janelaLegivel('09:00', '20:00'), 'das 09:00 às 20:00')
  assert.equal(A.janelaLegivel(null, null), 'o dia todo')
  const duas = aut({ codigo: 'shopee_duvida_2h', nome: '"Ficou alguma dúvida?" (2 h)', seguinte: 'shopee_duvida_26h' })
  const vinte6 = aut({ codigo: 'shopee_duvida_26h', gatilho: 'seguinte' })
  assert.equal(A.gatilhoLegivel(vinte6, [duas, vinte6]), 'depois de "Ficou alguma dúvida?" (2 h)')
  assert.equal(A.gatilhoLegivel(vinte6, []), 'depois da anterior')
  assert.equal(A.gatilhoLegivel(aut({ gatilho: 'pedido_pago' })), 'pedido pago (Bling)')

  // O title da %: a conta inteira, os motivos traduzidos, o porquê da troca.
  const t = A.tituloConta(conta({
    total: 120, simulado: 100, pulado: 20, bateu_mandou: 95, bateu_nao_mandou: 15, so_davinci: 3, so_davinci_2d: 1, so_duoke: 2, combinada: 5,
    alertas: 1, casos: 115, precisao: 0.969, cobertura: 0.979, concordancia: 0.956, diferenca_mediana_s: 120, atraso_mediana_s: 120, motivos: { pessoa_respondeu: 12, xyz: 1 },
    por_que_nao: ['mandaria para quem devolveu, cancelou ou reclamou'],
  }), 'menu', '7 dias', { pessoa_respondeu: 'Alguém da equipe já respondeu' })
  assert.match(t, /^7 dias: 120 no registro — 100 mandaria, 20 não mandaria$/m)
  assert.match(t, /Bateu com o Duoke: 96,9% \(a menor entre precisão e cobertura: a precisão\) em 115 casos/)
  assert.match(t, /Precisão 96,9% .* cobertura 97,9% .* concordância 95,6% \(conta também "nenhum dos dois mandou"\)/)
  assert.match(t, /Só DaVinci: 3 \(1 nos últimos 2 dias\) · só Duoke: 2/)
  assert.match(t, /Diferenças combinadas \(fora da conta\): 5/)
  assert.match(t, /ALERTA: 1/)
  assert.match(t, /Horário: Duoke 2 min depois da nossa \(mediana\)/)
  assert.match(t, /Atraso do DaVinci: 2 min do gatilho até sair \(mediana\)/)
  assert.match(t, /Não mandaria por: Alguém da equipe já respondeu \(12\); xyz \(1\)/)
  assert.match(t, /Critério em 7 dias: ainda não — mandaria para quem devolveu/)
  const top = A.tituloConta(conta({ casos: 248, cobertura: 0.991, pode_trocar: true, so_davinci: 135 }), 'opcao', '7 dias')
  assert.match(top, /\(a cobertura\)/)
  assert.match(top, /nas opções a precisão não se mede pelo Duoke/)
  assert.match(top, /A mais que o Duoke: o DaVinci responderia na hora 135 vezes que o Duoke não respondeu/)
  assert.match(top, /Critério em 7 dias: passa .* a troca vale pelos últimos 7 dias de cada loja/)
  assert.equal(A.tituloConta(null, 'menu', '24 h'), '24 h: nada no registro.')

  // O seletor: "Enviar" desabilitado com o porquê (as frases, não os códigos).
  const travado = A.opcoesDeModo(loja(), aut())
  assert.deepEqual(travado.map((o) => [o.value, o.disabled]), [['desligado', false], ['simular', false], ['enviar', true]])
  assert.equal(travado[2].label, 'Enviar (travado)')
  assert.match(travado[2].title, /^Travado: o envio das automáticas está desligado no servidor \(ATENDIMENTO_AUTOMACOES_ENVIO\); o envio geral pelo DaVinci está desligado \(ATENDIMENTO_ENVIO_ATIVO\)$/)
  const livre = A.opcoesDeModo(loja({ pode_enviar: true, por_que_nao_enviar: [] }), aut())
  assert.equal(livre[2].disabled, false)
  // Já em Enviar (o disjuntor, a chave desligada depois): dá para continuar e sair.
  assert.equal(A.opcoesDeModo(loja({ regra: regra({ modo: 'enviar' }) }), aut())[2].disabled, false)
  // A opção sem texto não sai de Desligado.
  assert.deepEqual(A.opcoesDeModo(loja({ regra: regra({ modo: 'desligado' }) }), aut({ travada: true })).map((o) => o.disabled), [false, true, true])
  assert.equal(A.motivosEnviar(['campanha_sem_auto_reply', 'novo_codigo'])[1], 'novo_codigo', 'código novo aparece cru')
  // ENVIAR com a chave desligada: o vermelho da linha.
  assert.equal(A.enviarSemChave(loja({ regra: regra({ modo: 'enviar' }) }), CHAVES), true)
  assert.equal(A.enviarSemChave(loja({ regra: regra({ modo: 'enviar' }) }), { ...CHAVES, envio_automacoes: true, envio_geral: true }), false)
  assert.equal(A.enviarSemChave(loja(), CHAVES), false)
  assert.deepEqual(A.contarModos([loja(), loja({ regra: regra({ modo: 'desligado' }) }), loja()]), { desligado: 1, simular: 2, enviar: 0 })
  const ls = [loja(), loja({ integration_id: 'i-2', loja: 'KFA', integracao: 'kfa', duoke_hoje: false, regra: regra({ modo: 'desligado' }) })]
  assert.deepEqual(A.lojasVisiveis(ls, 'kf', 'todas').map((l) => l.loja), ['KFA'])
  assert.deepEqual(A.lojasVisiveis(ls, '', 'ligadas').map((l) => l.loja), ['Barbosa'])
  assert.deepEqual(A.lojasVisiveis(ls, '', 'duoke').map((l) => l.loja), ['Barbosa'])
  assert.equal(A.faltaSimularNoDuoke([loja({ regra: regra({ modo: 'desligado' }) }), loja(), ls[1]]), 1)

  // As chaves do servidor em chips (a Shopee tem o auto_reply).
  const chips = A.chipsDasChaves(CHAVES, 'shopee')
  assert.deepEqual(chips.map((c) => c.chave), ['motor', 'envio_automacoes', 'envio_geral', 'auto_reply', 'teto'])
  assert.equal(chips.find((c) => c.chave === 'envio_automacoes').texto, 'Envio das automáticas desligado')
  assert.match(chips.find((c) => c.chave === 'envio_automacoes').hint, /ATENDIMENTO_AUTOMACOES_ENVIO/)
  assert.deepEqual(A.chipsDasChaves({ ...CHAVES, leitura_ativa: false, motor_ativo: false }, 'tiktok').map((c) => [c.chave, c.tom]),
    [['leitura', 'atencao'], ['motor', 'atencao'], ['envio_automacoes', 'neutro'], ['envio_geral', 'neutro'], ['teto', 'neutro']])
  assert.equal(A.chipsDasChaves({ ...CHAVES, envio_automacoes: true }, 'ml').find((c) => c.chave === 'envio_automacoes').tom, 'atencao')
  // A chave do auto_reply ligada SEM o envio por ele: as campanhas continuam fora.
  const semEnvio = A.chipsDasChaves({ ...CHAVES, shopee_auto_reply: true }, 'shopee').find((c) => c.chave === 'auto_reply')
  assert.equal(semEnvio.texto, 'Resposta automática da Shopee: falta o envio por ela')
  assert.equal(semEnvio.tom, 'atencao')
  assert.match(semEnvio.hint, /nunca saem como mensagem normal/)
  assert.equal(A.chipsDasChaves({ ...CHAVES, shopee_auto_reply: true, shopee_auto_reply_adaptador: true }, 'shopee').find((c) => c.chave === 'auto_reply').tom, 'ok')
  // A troca é por loja: a automação só conta as lojas ligadas prontas.
  assert.deepEqual(A.lojasProntas([loja({ pode_trocar: true }), loja(), loja({ regra: regra({ modo: 'desligado' }), pode_trocar: true })]), { prontas: 1, ligadas: 2 })
  assert.match(A.tituloTroca(loja()), /^Troca nesta loja \(últimos 7 dias\): ainda não — poucos casos \(0 de 30\)$/)
  assert.match(A.tituloTroca(loja({ pode_trocar: true, por_que_nao_trocar: [] })), /^Pronta para trocar nesta loja/)
  assert.deepEqual(A.chipsDasChaves(null, 'shopee'), [])

  // As {lacunas}.
  assert.equal(A.lacuna('comprador'), '{comprador}')
  assert.deepEqual(A.placeholdersDoTexto('Oi, {comprador}! {Comprador} {{ rastreio }} {comprador}'), ['comprador', 'rastreio'])
  assert.deepEqual(A.placeholdersDesconhecidos('Oi, {comprador}! Seu {rastreio}', ['comprador']), ['rastreio'])
  assert.equal(A.tamanhoComExemplo('Oi, {comprador}!', 'shopee'), 'Oi, maria.silva!'.length)
  assert.equal(A.tamanhoComExemplo('“Oi” 😊', 'ml'), '"Oi"'.length, 'conta como o ML recebe')

  // O formulário e o corpo do PATCH: só o que mudou.
  const a = aut()
  const r = regra({ teto_dia: 50, janela_inicio: '09:00', janela_fim: '20:00' })
  const f = A.formDaRegra(r)
  assert.equal(f.dia_todo, false)
  assert.equal(f.teto_dia, 50)
  assert.deepEqual(A.corpoDaRegra(f, r, a), {}, 'nada mudou')
  f.partes[0].texto = `${MENU}   `
  assert.deepEqual(A.corpoDaRegra(f, r, a), {}, 'espaço sobrando não é mudança')
  f.partes[0].texto = 'Oi, {comprador}! Escolha: 1, 2 ou 6.'
  f.atraso_min = 3
  f.janela_fim = '22:00'
  f.condicoes.sessao_h = 6
  f.condicoes.pular_em_disputa_com_pessoa = false
  f.teto_dia = ''
  assert.deepEqual(A.corpoDaRegra(f, r, a), {
    partes: [{ tipo: 'texto', texto: 'Oi, {comprador}! Escolha: 1, 2 ou 6.' }],
    atraso_min: 3,
    janela_inicio: '09:00',
    janela_fim: '22:00',
    condicoes: { sessao_h: 6, pular_em_disputa_com_pessoa: false },
    sem_teto: true,
  })
  f.dia_todo = true
  assert.equal(A.corpoDaRegra(f, r, a).sem_janela, true)
  assert.equal(A.corpoDaRegra(f, r, a).janela_inicio, undefined)
  const semJanela = regra()
  const f2 = A.formDaRegra(semJanela)
  assert.equal(f2.dia_todo, true)
  f2.dia_todo = false
  assert.deepEqual(A.corpoDaRegra(f2, semJanela, a), { janela_inicio: '09:00', janela_fim: '20:00' })
  f2.dia_todo = true
  f2.teto_dia = 30
  assert.deepEqual(A.corpoDaRegra(f2, semJanela, a), { teto_dia: 30 })
  // O cartão e a figurinha vão como estão (só o texto muda).
  const comCartao = regra({ partes: [{ tipo: 'cartao_pedido' }, { tipo: 'texto', texto: 'Oi!' }, { tipo: 'figurinha', figurinha: '0007', pacote: 'br_shoppito' }] })
  const f3 = A.formDaRegra(comCartao)
  f3.partes[1].texto = 'Olá!'
  assert.deepEqual(A.corpoDaRegra(f3, comCartao, a).partes, [{ tipo: 'cartao_pedido' }, { tipo: 'texto', texto: 'Olá!' }, { tipo: 'figurinha', figurinha: '0007', pacote: 'br_shoppito' }])
  assert.equal(f3.partes[0] === comCartao.partes[0], false, 'o formulário é uma cópia')

  // O que segura o salvar.
  const ok = A.formDaRegra(regra())
  assert.deepEqual(A.problemasDoForm(ok, a), [])
  const ruim = A.formDaRegra(regra({ partes: [{ tipo: 'texto', texto: '  ' }, { tipo: 'texto', texto: 'Oi {nome}, seu {rastreio}' }] }))
  ruim.atraso_min = 20000
  ruim.dia_todo = false
  ruim.janela_inicio = '20:00'
  ruim.janela_fim = '09:00'
  ruim.teto_dia = -1
  ruim.condicoes.sessao_h = 0
  assert.deepEqual(A.problemasDoForm(ruim, a), [
    'Texto 1: escreva a mensagem',
    'Texto 2: {nome}, {rastreio} não existe (as lacunas são: {comprador})',
    'Atraso: de 0 a 10080 minutos (7 dias)',
    'Horário: o início tem que ser antes do fim (a janela não atravessa a meia-noite)',
    'Teto: de 0 a 6000 por dia (vazio = o teto geral)',
    'O menu vale por: um número maior que zero',
  ])
  ruim.janela_inicio = '9h'
  assert.ok(A.problemasDoForm(ruim, a).includes('Horário: use HH:MM'))
  // O ML: até 350 caracteres, contados com o nome de exemplo.
  const ml = aut({ plataforma: 'ml', canal: 'pos_venda' })
  const longo = A.formDaRegra(regra({ partes: [{ tipo: 'texto', texto: `{comprador} ${'x'.repeat(340)}` }] }))
  assert.deepEqual(A.problemasDoForm(longo, ml), ['Texto: passa de 350 caracteres (com o nome de exemplo)'])
  assert.deepEqual(A.problemasDoForm(longo, a), [], 'na Shopee cabe (1000)')

  // A comparação de cada linha = a conta do comparador.
  const cmp = (estado, duoke, o = {}) => A.comparacaoDaLinha({ estado, duoke, divergencia: null, ...o })
  assert.equal(cmp('simulado', 'mandou'), 'bateu')
  assert.equal(cmp('enviado', 'mandou'), 'bateu')
  assert.equal(cmp('revisar', 'mandou'), 'bateu')
  assert.equal(cmp('pulado', 'nao_mandou'), 'bateu')
  assert.equal(cmp('simulado', 'nao_mandou'), 'so_davinci')
  assert.equal(cmp('pulado', 'mandou'), 'so_duoke')
  assert.equal(cmp('so_duoke', 'mandou'), 'so_duoke')
  assert.equal(cmp('simulado', 'mandou', { divergencia: 'horario_comercial' }), 'combinada')
  assert.equal(cmp('simulado', 'pendente'), 'pendente')
  assert.equal(cmp('agendado', 'pendente'), null)
  assert.equal(cmp('falhou', 'nao_se_aplica'), null)
  assert.equal(A.comparacaoInfo({ estado: 'simulado', duoke: 'nao_mandou', divergencia: null }).label, 'só DaVinci')
  assert.equal(A.comparacaoInfo({ estado: 'agendado', duoke: 'pendente', divergencia: null }), null)
  // O estado: "não saiu" em vermelho quando a regra está em Enviar com a chave desligada.
  assert.equal(A.estadoDaLinha({ estado: 'simulado', modo: 'enviar', motivo: 'envio_desligado' }).label, 'não saiu')
  assert.match(A.estadoDaLinha({ estado: 'simulado', modo: 'enviar', motivo: 'envio_desligado' }).cls, /red/)
  assert.equal(A.estadoDaLinha({ estado: 'simulado', modo: 'simular', motivo: null }).label, 'mandaria')
  assert.equal(A.estadoDaLinha({ estado: 'pulado', modo: 'simular', motivo: 'ja_mandado' }).label, 'não mandaria')
  assert.equal(A.estadoDaLinha({ estado: 'pulado', modo: 'enviar', motivo: 'ja_mandado' }).label, 'não mandou')
  assert.equal(A.estadoDaLinha({ estado: 'novo', modo: null, motivo: null }).label, 'novo')
  assert.equal(A.alvoDaLinha({ alvo: 'pedido', pedido: '2510050ABC' }), 'pedido 2510050ABC')
  assert.equal(A.alvoDaLinha({ alvo: 'comprador', pedido: null }), 'comprador')
  assert.equal(A.alvoDaLinha({ alvo: 'duoke', pedido: '99' }), 'mensagem do Duoke · pedido 99')

  // Os parâmetros do registro.
  const vazio = A.filtrosRegistroVazios()
  assert.equal(A.paramsRegistro(vazio), 'limite=200')
  assert.equal(A.paramsRegistro({ ...vazio, automacao: 'shopee_menu', integration_id: 'i-1', estado: 'pulado', comparacao: 'so_davinci' }, ISO),
    `automacao=shopee_menu&integration_id=i-1&estado=pulado&so=so_davinci&limite=200&antes=${encodeURIComponent(ISO)}`)
  assert.equal(A.paramsRegistro({ ...vazio, comparacao: 'duoke:pendente' }), 'duoke=pendente&limite=200')

  // Os erros das rotas da aba, em português.
  const e409 = { data: { detail: { code: 'envio_desligado', motivos: ['envio_desligado', 'envio_geral_desligado', 'loja_sem_acesso'], detail: 'Esta regra não pode ir para ENVIAR agora.' } } }
  assert.deepEqual(A.erroDaAutomacao(e409), {
    texto: 'Não dá para pôr em Enviar: o envio das automáticas está desligado no servidor (ATENDIMENTO_AUTOMACOES_ENVIO).',
    motivos: ['o envio geral pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO)', 'a leitura desta loja está sem acesso ou desligada'],
  })
  assert.deepEqual(A.erroDaAutomacao({ data: { detail: { code: 'texto_invalido', motivos: ['contato fora da plataforma (whatsapp)'] } } }),
    { texto: 'O texto não passa no validador', motivos: ['contato fora da plataforma (whatsapp)'] })
  assert.deepEqual(A.erroDaAutomacao({ data: { detail: { code: 'confirmar_duoke', detail: 'Marque que desligou…' } } }).texto, 'Marque que você já desligou esta automação desta loja no Duoke.')
  assert.deepEqual(A.erroDaAutomacao({ data: { detail: { code: 'condicao_desconhecida', condicoes: ['sessao_h', 'xis'] } } }).motivos, ['O menu vale por', 'xis'])
  assert.deepEqual(A.erroDaAutomacao({ data: { detail: { code: 'condicao_invalida', condicao: 'intervalo_h' } } }).motivos, ['No máximo um a cada'])
  assert.equal(A.erroDaAutomacao({ data: { detail: { code: 'sem_texto' } } }).texto, A.ERROS_AUTOMACAO.sem_texto)
  // O resto é o erroDaApi de sempre (rede, 403, código de outra rota).
  assert.deepEqual(A.erroDaAutomacao({}, 'Não consegui'), P.erroDaApi({}, 'Não consegui'))
  assert.deepEqual(A.erroDaAutomacao({ statusCode: 403 }, 'x'), { texto: 'Sem permissão para isso.', motivos: [] })
  assert.equal(A.erroDaAutomacao({ data: { detail: { code: 'forbidden' } } }).texto, P.ERROS.forbidden)
}

// ------------------------------------------------ a tela com a API falsa
const ICONES = ((setupSrc.match(/import \{([^}]*)\} from 'lucide-vue-next'/) || [])[1] || '').split(',').map((s) => s.trim()).filter(Boolean)
assert.ok(ICONES.includes('Lock') && ICONES.includes('Zap'))
const NOMES_SETUP = [...new Set([...setupSrc.matchAll(/^(?:const|let|function|async function) ([A-Za-z_$][\w$]*)/gm)].map((m) => m[1]))]
const tique = () => new Promise((r) => setTimeout(r, 0))
const icone = { render: () => Vue.h('i') }

function montar({ rotas, canEdit = true, confirmar = () => true, armazenamento = null }) {
  const src = setupSrc.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const js = `${transpile(src)}\nreturn { ${NOMES_SETUP.join(', ')} }`
  const chamadas = []
  const avisos = []
  const perguntas = []
  const emitidos = []
  const montados = []
  const polls = []
  const inesperadas = []
  const fakeApi = async (url, opts) => {
    chamadas.push({ url, metodo: opts?.method || 'GET', body: opts?.body })
    const chave = `${opts?.method || 'GET'} ${url}`
    const r = rotas[chave] ?? rotas[`${opts?.method || 'GET'} ${url.split('?')[0]}`]
    if (r === undefined) inesperadas.push(chave)
    assert.ok(r !== undefined, `rota inesperada: ${chave}`)
    return typeof r === 'function' ? r(opts, url) : JSON.parse(JSON.stringify(r))
  }
  const toast = (tipo) => (titulo, detalhe) => avisos.push([tipo, titulo, detalhe])
  const props = Vue.reactive({ canEdit })
  const globais = {
    ...A,
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, reactive: Vue.reactive, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn),
    defineProps: () => props,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({ api: fakeApi }),
    useToasts: () => ({ success: toast('ok'), error: toast('erro'), warning: toast('aviso'), info: toast('info') }),
    useRelogio: () => Vue.ref(AGORA),
    usePollingVisivel: (fn) => { polls.push(fn); return { marcar() {} } },
    confirm: (t) => { perguntas.push(t); return confirmar(t) },
    localStorage: armazenamento,
    erroDaApi: P.erroDaApi, fmtDataHora: P.fmtDataHora, haQuanto: P.haQuanto,
  }
  for (const n of ICONES) globais[n] = icone
  const nomes = Object.keys(globais)
  const vm = new Function(...nomes, js)(...nomes.map((n) => globais[n]))
  for (const fn of montados) fn()
  return { vm, props, chamadas, avisos, perguntas, emitidos, polls, inesperadas }
}
// Nenhuma chamada fora das rotas do cenário (e nenhuma de envio: só as da aba).
function semSurpresa(m) {
  assert.deepEqual(m.inesperadas, [], 'chamada fora do esperado')
  for (const c of m.chamadas) assert.match(c.url, /^\/api\/atendimento\/automacoes(\?|\/)/, c.url)
}
function armazenamentoFalso(inicial = {}) {
  const m = new Map(Object.entries(inicial))
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), mapa: m }
}
const tpl = compileTemplate({ source: tela, filename: autSfc.filename, id: 'automaticas' })
assert.deepEqual(tpl.errors, [])
async function render(m) {
  const mod = {}
  new Function('exports', 'require', transpile(tpl.code))(mod, require)
  const estado = {}
  for (const n of ICONES) estado[n] = icone
  const importados = { limiteDe: P.limiteDe, fmtDataHora: P.fmtDataHora, haQuanto: P.haQuanto }
  const app = Vue.createSSRApp({ setup: () => ({ ...A, ...importados, ...estado, ...m.vm, canEdit: m.props.canEdit }), render: mod.render })
  app.component('Button', { props: ['disabled', 'variant', 'size', 'class'], setup: (p, { slots, attrs }) => () => Vue.h('button', { ...attrs, disabled: p.disabled || undefined }, slots.default?.()) })
  app.component('EmptyState', { props: ['title', 'description', 'icon'], setup: (p, { attrs }) => () => Vue.h('div', { ...attrs, 'data-empty': '' }, `${p.title} — ${p.description}`) })
  app.component('AtendimentoPlataforma', { props: ['codigo'], setup: (p) => () => Vue.h('span', { 'data-plataforma': p.codigo }, p.codigo) })
  for (const n of ICONES) app.component(n, icone)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

const GET_LISTA = (p = 'shopee', d = 7) => `GET /api/atendimento/automacoes?plataforma=${p}&dias=${d}`
const GET_REG = (q = 'limite=200') => `GET /api/atendimento/automacoes/registro?${q}`
const PATCH = (codigo, integ) => `PATCH /api/atendimento/automacoes/${codigo}/${integ}`
// O que o comprador escreveu NUNCA vem da API; se viesse, a tela não mostraria.
const SEGREDO = 'SEGREDO-DO-COMPRADOR cpf 123.456.789-00'
const NOME_COMPRADOR = 'fulana.secreta'

function cenario() {
  const kfa = loja({
    integration_id: 'i-kfa', loja: 'KFA', integracao: 'kfa', pode_enviar: true, por_que_nao_enviar: [],
    h24: conta({ simulado: 12 }), periodo: conta({ simulado: 90, so_davinci: 2, so_davinci_2d: 1, so_duoke: 1, concordancia: 0.98, cobertura: 0.99, precisao: 0.988, casos: 100 }),
    ultimo: { devido_em: '2026-10-05T11:55:00+00:00', estado: 'pulado', motivo: 'pessoa_respondeu' },
    pode_trocar: true, por_que_nao_trocar: [],
  })
  const barbosa = loja({
    h24: conta({ simulado: 3 }), periodo: conta({ simulado: 20, concordancia: 0.9, precisao: 0.7, cobertura: 0.8, casos: 25, alertas: 1, por_que_nao: ['poucos casos (25 de 30)'] }),
    por_que_nao_trocar: ['poucos casos (25 de 30)', 'mandaria para quem devolveu, cancelou ou reclamou'],
  })
  const enviando = loja({ integration_id: 'i-mini', loja: 'Mini', integracao: 'mini', duoke_hoje: false, regra: regra({ modo: 'enviar', enviar_desde: ISO }), periodo: conta({ enviado: 4 }) })
  const desligada = loja({ integration_id: 'i-vita', loja: 'Vita', integracao: 'vita', regra: regra({ modo: 'desligado', padrao: true, id: null, versao: 0 }) })
  return resposta({
    automacoes: [
      aut({ lojas: [barbosa, kfa, enviando, desligada], total_24h: conta({ simulado: 15 }), total_periodo: conta({ simulado: 110, enviado: 4, so_davinci: 2, so_duoke: 1, alertas: 1, concordancia: 0.97, precisao: 0.97, cobertura: 0.96, casos: 125, pode_trocar: true }) }),
      aut({ codigo: 'shopee_opcao_4', nome: 'Resposta da opção 4 (troca de endereço)', tipo: 'opcao', gatilho: 'opcao', travada: true, diferenca_combinada: 'na_hora', condicoes_padrao: { sessao_h: 12, opcao_sem_sessao: true }, lojas: [loja({ regra: regra({ modo: 'desligado', partes: [] }), por_que_nao_enviar: ['sem_texto', ...TRAVADO] })] }),
      aut({ codigo: 'shopee_entregue', nome: 'Entregue', tipo: 'entregue', gatilho: 'entregue', alvo: 'pedido', familia: 'pedido', campanha: true, diferenca_combinada: 'horario_comercial', atraso_min: 0, janela_inicio: '09:00', janela_fim: '20:00', condicoes_padrao: {}, lojas: [loja({ regra: regra({ partes: [{ tipo: 'cartao_pedido' }, { tipo: 'texto', texto: 'Oi, {comprador}! Confirmamos a entrega.' }], janela_inicio: '09:00', janela_fim: '20:00', condicoes: {} }), por_que_nao_enviar: ['campanha_sem_auto_reply', ...TRAVADO] })] }),
    ],
  })
}
// O servidor falso guarda o estado: o PATCH muda a regra, e o GET seguinte a devolve.
function servidor() {
  const estado = cenario()
  return {
    estado,
    lista: () => JSON.parse(JSON.stringify(estado)),
    gravar(codigo, integ, mudou) {
      const l = estado.automacoes.find((a) => a.codigo === codigo).lojas.find((x) => x.integration_id === integ)
      Object.assign(l, JSON.parse(JSON.stringify(mudou)))
    },
  }
}
const REGISTRO = {
  linhas: [
    { ...linhaReg(), texto: SEGREDO, comprador_nome: NOME_COMPRADOR },
    linhaReg({ id: 'l-2', automacao: 'shopee_entregue', automacao_nome: 'Entregue', alvo: 'pedido', pedido: '2510050ABC', conversa_id: null, estado: 'pulado', motivo: 'devolucao', motivo_texto: 'Devolução ou reembolso no pedido', duoke: 'nao_mandou', duoke_diferenca_s: null }),
    linhaReg({ id: 'l-3', estado: 'simulado', duoke: 'nao_mandou', alerta: 'devolucao', alerta_texto: 'Mandaria para quem devolveu ou pediu reembolso', duoke_diferenca_s: null }),
    linhaReg({ id: 'l-4', estado: 'simulado', duoke: 'mandou', divergencia: 'horario_comercial', divergencia_texto: 'Entregue: o DaVinci manda das 9h às 20h; o Duoke de madrugada' }),
  ],
  proximo: '2026-10-05T10:00:00+00:00',
}

async function principal() {
  // 1. Abre: lê a lista e o registro; nada de envio; a faixa e o "Enviar" travado.
  {
    const m = montar({ rotas: { [GET_LISTA()]: cenario(), [GET_REG()]: REGISTRO } })
    await tique()
    assert.deepEqual(m.chamadas.map((c) => `${c.metodo} ${c.url}`), [GET_LISTA(), GET_REG()])
    assert.equal(m.polls.length, 1, 'a conta se atualiza sozinha (com a aba visível)')
    let html = await render(m)
    assert.match(html, /data-faixa-automaticas[^>]*data-nivel="info"/)
    assert.ok(html.includes(FAIXA_SECO), 'o texto da faixa é o da API')
    assert.match(html, /data-chave="envio_automacoes"[^>]*>Envio das automáticas desligado</)
    assert.match(html, /data-chave="auto_reply"/)
    assert.match(html, /data-trava-geral[\s\S]*Enviar está travado em todas as lojas:[\s\S]*ATENDIMENTO_AUTOMACOES_ENVIO/)
    // As seções: fechadas (só os totais), na ordem da API.
    assert.deepEqual([...html.matchAll(/data-automacao="([a-z_0-9]+)"/g)].map((x) => x[1]), ['shopee_menu', 'shopee_opcao_4', 'shopee_entregue'])
    assert.doesNotMatch(html, /data-loja=/, 'fechadas por padrão')
    assert.match(html, /bateu com o Duoke:<\/span> <span class="tabular-nums font-semibold text-emerald-700 dark:text-emerald-300">96%</)
    assert.match(html, />só DaVinci 2</)
    assert.match(html, /1 alerta\(s\)/)
    assert.match(html, /data-selo-combinada[^>]*>diferença combinada</)
    assert.match(html, /title="Sai das 9h às 20h \(o Duoke manda de madrugada\)"/)
    assert.match(html, />sem texto</, 'a opção 4 sem texto')
    assert.match(html, />das 09:00 às 20:00</)
    assert.match(html, />campanha</)
    // O botão "simular nas lojas do Duoke": só onde há loja do Duoke desligada.
    const botoes = [...html.matchAll(/<button([^>]*)data-simular-duoke([^>]*)>/g)].map((x) => x[0])
    assert.equal(botoes.length, 2, 'não na opção sem texto')
    assert.doesNotMatch(botoes[0], /disabled/, 'o menu tem a Vita desligada')
    assert.match(botoes[1], /disabled/, 'o entregue não tem o que ligar')

    // Aberta: uma linha por loja.
    m.vm.alternarAutomacao('shopee_menu')
    html = await render(m)
    assert.deepEqual([...html.matchAll(/data-loja="([^"]+)"/g)].map((x) => x[1]), ['i-barbosa', 'i-kfa', 'i-mini', 'i-vita'])
    const linhaDe = (id) => {
      const i = html.indexOf(`data-loja="${id}"`)
      const j = html.indexOf('data-loja=', i + 10)
      return html.slice(i, j < 0 ? undefined : j)
    }
    const barbosa = linhaDe('i-barbosa')
    // "Enviar" desabilitado, com o motivo.
    assert.match(barbosa, /<option value="enviar" disabled title="Travado: o envio das automáticas está desligado no servidor \(ATENDIMENTO_AUTOMACOES_ENVIO\); o envio geral pelo DaVinci está desligado \(ATENDIMENTO_ENVIO_ATIVO\)">Enviar \(travado\)<\/option>/)
    assert.match(barbosa, /<option value="simular" selected title="[^"]*">Simular<\/option>/, "o modo de agora selecionado")
    assert.match(barbosa, /aria-label="Enviar travado: o envio das automáticas/)
    assert.match(barbosa, />Duoke hoje</)
    assert.match(barbosa, /class="tabular-nums font-semibold text-red-600 dark:text-red-400" data-bateu>70%</)
    assert.match(barbosa, />alerta</)
    const kfaHtml = linhaDe('i-kfa')
    assert.match(kfaHtml, /<option value="enviar" title="manda de verdade[^"]*">Enviar<\/option>/, 'a API deixa: Enviar liberado')
    assert.match(kfaHtml, /class="tabular-nums font-semibold text-emerald-700 dark:text-emerald-300" data-bateu>98,8%</)
    assert.match(kfaHtml, />24 h<\/span>12<\/div>/)
    assert.match(kfaHtml, /há 5 min<\/span>\s*· <span title="Alguém da equipe já respondeu">não mandaria</)
    assert.match(linhaDe('i-mini'), /data-enviar-sem-chave[^>]*>ENVIAR sem envio</, 'ENVIAR com a chave desligada: vermelho')
    assert.match(linhaDe('i-vita'), />padrão</)

    // O registro: sem texto de comprador, com o estado, o Duoke e a comparação.
    assert.ok(!html.includes('SEGREDO') && !html.includes('123.456.789') && !html.includes(NOME_COMPRADOR), 'nada do comprador na tela')
    assert.deepEqual([...html.matchAll(/data-registro-linha="([^"]+)"/g)].map((x) => x[1]), ['l-1', 'l-2', 'l-3', 'l-4'])
    assert.match(html, /data-comparacao[^>]*>bateu</)
    assert.match(html, />4 min depois</, 'na coluna do Duoke, sem repetir o nome')
    assert.match(html, />pedido 2510050ABC</)
    assert.match(html, />Devolução ou reembolso no pedido</)
    assert.match(html, /data-alerta[^>]*>.*Mandaria para quem devolveu ou pediu reembolso</)
    assert.match(html, />Entregue: o DaVinci manda das 9h às 20h; o Duoke de madrugada</)
    assert.equal((html.match(/abrir a conversa na Caixa/g) || []).length, 3, 'só onde há conversa')
    assert.match(html, /carregar mais/)
    // O template não pede texto nenhum ao registro.
    assert.doesNotMatch(tela, /\bl\.(texto|comprador|nome|mensagem)\b/)
    semSurpresa(m)
  }

  // 2. Modo: simular → desligado (PATCH) e o "Enviar" travado não abre nada.
  {
    let corpo = null
    const srv = servidor()
    const m = montar({
      rotas: {
        [GET_LISTA()]: srv.lista, [GET_REG()]: { linhas: [], proximo: null },
        [PATCH('shopee_menu', 'i-barbosa')]: (opts) => {
          corpo = opts.body
          const r = { integration_id: 'i-barbosa', automacao: 'shopee_menu', regra: regra({ modo: 'desligado', ligada_desde: null }), rearmadas: 0, pode_enviar: false, por_que_nao_enviar: TRAVADO }
          srv.gravar('shopee_menu', 'i-barbosa', { regra: r.regra })
          return r
        },
      },
    })
    await tique()
    const a = m.vm.automacoes.value[0]
    const b = a.lojas[0]
    const ev = (value) => ({ target: { value } })
    m.vm.aoEscolherModo(a, b, ev('desligado'))
    await tique()
    assert.deepEqual(corpo, { modo: 'desligado' })
    assert.equal(m.vm.automacoes.value[0].lojas[0].regra.modo, 'desligado', 'a linha muda pela resposta da API')
    assert.deepEqual(m.chamadas.slice(2).map((c) => `${c.metodo} ${c.url}`), [PATCH('shopee_menu', 'i-barbosa'), GET_LISTA()], 'e a lista é relida depois (a atualização que saiu antes não volta o modo)')
    assert.equal(m.avisos.at(-1)[0], 'ok')
    assert.deepEqual(m.perguntas, [], 'sem confirmação no modo seco')
    // "Enviar" travado (a opção já vem desabilitada): nem abre a confirmação.
    const n = m.chamadas.length
    const alvo = ev('enviar')
    m.vm.aoEscolherModo(a, m.vm.automacoes.value[0].lojas[0], alvo)
    assert.equal(alvo.target.value, 'desligado', 'o seletor volta ao modo de agora')
    assert.equal(m.vm.pedindoEnviar.value, null)
    assert.equal(m.chamadas.length, n)
    assert.equal(m.avisos.at(-1)[0], 'aviso')
    assert.deepEqual(m.avisos.at(-1)[2], A.motivosEnviar(TRAVADO))
    semSurpresa(m)
  }

  // 3. Enviar liberado pela API: só com "desliguei no Duoke"; a API recusa (409) → erro em português.
  {
    const corpos = []
    let recusar = true
    const srv = servidor()
    const m = montar({
      rotas: {
        [GET_LISTA()]: srv.lista, [GET_REG()]: { linhas: [], proximo: null },
        [PATCH('shopee_menu', 'i-kfa')]: (opts) => {
          corpos.push(opts.body)
          if (recusar) throw Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'envio_desligado', motivos: ['envio_desligado'], detail: 'x' } } })
          const r = { integration_id: 'i-kfa', automacao: 'shopee_menu', regra: regra({ modo: 'enviar', enviar_desde: ISO }), rearmadas: 2, pode_enviar: true, por_que_nao_enviar: [] }
          srv.gravar('shopee_menu', 'i-kfa', { regra: r.regra })
          return r
        },
      },
    })
    await tique()
    const a = m.vm.automacoes.value[0]
    const kfa = a.lojas[1]
    m.vm.aoEscolherModo(a, kfa, { target: { value: 'enviar' } })
    assert.equal(m.vm.pedindoEnviar.value, 'shopee_menu:i-kfa', 'abre a confirmação na linha')
    m.vm.alternarAutomacao('shopee_menu')
    let html = await render(m)
    const quadro = html.slice(html.indexOf('data-pedir-enviar'))
    assert.match(quadro, /Desliguei esta automação desta loja no Duoke/)
    assert.match(quadro, /<button disabled><i[^>]*><\/i>\s*pôr em Enviar/, 'o botão espera a marcação')
    await m.vm.confirmarEnviar(a, kfa)
    assert.deepEqual(corpos, [], 'sem a marcação, nada')
    m.vm.desligueiNoDuoke.value = true
    await m.vm.confirmarEnviar(a, kfa)
    assert.deepEqual(corpos, [{ modo: 'enviar', desliguei_no_duoke: true }])
    assert.deepEqual(m.avisos.at(-1).slice(0, 2), ['erro', 'Não dá para pôr em Enviar: o envio das automáticas está desligado no servidor (ATENDIMENTO_AUTOMACOES_ENVIO).'])
    assert.equal(m.vm.pedindoEnviar.value, 'shopee_menu:i-kfa', 'recusado: a confirmação fica')
    assert.equal(m.vm.automacoes.value[0].lojas[1].regra.modo, 'simular')
    assert.equal(m.chamadas.filter((c) => c.metodo === 'GET' && c.url.includes('plataforma=')).length, 1, 'recusado: nada a reler')
    recusar = false
    await m.vm.confirmarEnviar(a, kfa)
    await tique()
    assert.equal(m.vm.pedindoEnviar.value, null)
    assert.equal(m.vm.automacoes.value[0].lojas[1].regra.modo, 'enviar')
    assert.match(m.avisos.at(-1)[2].join(' '), /2 que o Duoke não mandou voltaram para a fila/)
    // Sair de Enviar pede confirmação (o comprador pode ficar sem); "não" = nada.
    const m2 = montar({ rotas: { [GET_LISTA()]: cenario(), [GET_REG()]: { linhas: [], proximo: null } }, confirmar: () => false })
    await tique()
    const a2 = m2.vm.automacoes.value[0]
    m2.vm.aoEscolherModo(a2, a2.lojas[2], { target: { value: 'simular' } })
    assert.match(m2.perguntas[0], /Tirar "Menu "selecione sua dúvida"" de ENVIAR em Mini\?[\s\S]*religue no Duoke antes/)
    assert.equal(m2.chamadas.filter((c) => c.metodo !== 'GET').length, 0)
    html = await render(m2)
    assert.ok(html.length > 0)
    semSurpresa(m)
    semSurpresa(m2)
  }

  // 4. "Simular nas lojas do Duoke": confirma, POST, relê a lista.
  {
    const m = montar({ rotas: { [GET_LISTA()]: cenario(), [GET_REG()]: { linhas: [], proximo: null }, 'POST /api/atendimento/automacoes/shopee_menu/simular-nas-lojas-do-duoke': { criadas: 1, ligadas: 0, mantidas: 2 } } })
    await tique()
    await m.vm.simularNasLojasDoDuoke(m.vm.automacoes.value[0])
    assert.match(m.perguntas[0], /SIMULAR nas 1 loja\(s\)[\s\S]*Nada é enviado/)
    assert.deepEqual(m.chamadas.map((c) => `${c.metodo} ${c.url}`).slice(2), ['POST /api/atendimento/automacoes/shopee_menu/simular-nas-lojas-do-duoke', GET_LISTA()])
    assert.match(m.avisos.at(-1)[2][0], /1 criada\(s\), 0 ligada\(s\), 2 já estavam ligadas/)
    // Sem loja do Duoke desligada: nem pergunta.
    const n = m.chamadas.length
    await m.vm.simularNasLojasDoDuoke(m.vm.automacoes.value[2])
    assert.equal(m.chamadas.length, n)
    semSurpresa(m)
  }

  // 5. O editor: prévia pelo backend, o que segura o salvar, o PATCH só com o que mudou.
  {
    const previas = []
    const patches = []
    let invalido = false
    const srv = servidor()
    const m = montar({
      rotas: {
        [GET_LISTA()]: srv.lista, [GET_REG()]: { linhas: [], proximo: null },
        'POST /api/atendimento/automacoes/previa': (opts) => {
          previas.push(opts.body)
          const t = opts.body.partes.find((p) => p.tipo === 'texto')?.texto || ''
          return { partes: [{ tipo: 'texto', texto: t.split('{comprador}').join('maria.silva') }], sem_nome: [{ tipo: 'texto', texto: t.replace(/,?\s*\{comprador\}/, '') }], motivos: /whats/i.test(t) ? ['contato fora da plataforma'] : [], comprador_exemplo: 'maria.silva' }
        },
        [PATCH('shopee_menu', 'i-barbosa')]: (opts) => {
          patches.push(opts.body)
          if (invalido) throw Object.assign(new Error('422'), { statusCode: 422, data: { detail: { code: 'texto_invalido', motivos: ['contato fora da plataforma (whatsapp)'] } } })
          const r = { integration_id: 'i-barbosa', automacao: 'shopee_menu', regra: regra({ partes: opts.body.partes, versao: 2 }), rearmadas: 0, pode_enviar: false, por_que_nao_enviar: TRAVADO }
          srv.gravar('shopee_menu', 'i-barbosa', { regra: r.regra })
          return r
        },
      },
    })
    await tique()
    const a = m.vm.automacoes.value[0]
    m.vm.alternarAutomacao('shopee_menu')
    m.vm.abrirEditor(a, a.lojas[0])
    await tique()
    assert.deepEqual(previas, [{ automacao: 'shopee_menu', integration_id: 'i-barbosa', partes: [{ tipo: 'texto', texto: MENU }] }])
    assert.equal(m.vm.mudou.value, false)
    let html = await render(m)
    assert.match(html, /data-editor/)
    assert.match(html, /data-placeholders[\s\S]*>\{comprador\}<\/button>[\s\S]*o usuário do comprador na plataforma/)
    assert.match(html, /data-previa[\s\S]*com o nome de exemplo maria\.silva/)
    assert.match(html, /passa no validador/)
    assert.match(html, /<button[^>]*disabled[^>]*data-salvar|<button[^>]*data-salvar[^>]*disabled/, 'nada mudou: salvar desabilitado')
    assert.match(html, />O menu vale por</)
    assert.match(html, /Não mandar em reclamação\/devolução com alguém da equipe atendendo/)
    // Lacuna que não existe: segura o salvar (sem chamar a API).
    m.vm.form.partes[0].texto = 'Oi {nome}! Escolha 1, 2 ou 6.'
    assert.deepEqual(m.vm.problemas.value, ['Texto: {nome} não existe (as lacunas são: {comprador})'])
    await m.vm.salvarRegra()
    assert.deepEqual(patches, [])
    // O {comprador} entra onde o cursor estava (sem cursor: no fim).
    m.vm.form.partes[0].texto = 'Oi, ! Escolha 1, 2 ou 6.'
    m.vm.inserirPlaceholder('comprador')
    assert.equal(m.vm.form.partes[0].texto, 'Oi, ! Escolha 1, 2 ou 6.{comprador}')
    m.vm.form.partes[0].texto = 'Oi, {comprador}! Escolha 1, 2 ou 6.'
    assert.deepEqual(m.vm.problemas.value, [])
    assert.deepEqual(m.vm.corpo.value, { partes: [{ tipo: 'texto', texto: 'Oi, {comprador}! Escolha 1, 2 ou 6.' }] })
    await m.vm.carregarPrevia()
    html = await render(m)
    assert.match(html, />Oi, maria\.silva! Escolha 1, 2 ou 6\.</)
    assert.match(html, /sem o nome do comprador[\s\S]*>Oi! Escolha 1, 2 ou 6\.</)
    await m.vm.salvarRegra()
    await tique()
    assert.deepEqual(patches, [{ partes: [{ tipo: 'texto', texto: 'Oi, {comprador}! Escolha 1, 2 ou 6.' }] }])
    assert.equal(m.vm.automacoes.value[0].lojas[0].regra.versao, 2)
    const iPatch = m.chamadas.findIndex((c) => c.metodo === 'PATCH')
    assert.ok(m.chamadas.slice(iPatch + 1).some((c) => `${c.metodo} ${c.url}` === GET_LISTA()), 'salvou: relê a lista')
    assert.equal(m.vm.mudou.value, false, 'salvo: o formulário é a regra nova')
    // O validador do backend recusa: o erro fica no editor, com os motivos.
    invalido = true
    m.vm.form.partes[0].texto = 'Chama no whats 11 99999-8888'
    await m.vm.carregarPrevia()
    html = await render(m)
    assert.match(html, /data-motivos-validador[\s\S]*contato fora da plataforma/)
    await m.vm.salvarRegra()
    assert.deepEqual(m.vm.erroSalvar.value, { texto: 'O texto não passa no validador', motivos: ['contato fora da plataforma (whatsapp)'] })
    html = await render(m)
    assert.match(html, /role="alert"[\s\S]*O texto não passa no validador[\s\S]*contato fora da plataforma \(whatsapp\)/)
    m.vm.desfazer()
    assert.equal(m.vm.mudou.value, false)
    // Só leitura: o editor abre para ver, sem salvar.
    m.props.canEdit = false
    html = await render(m)
    assert.doesNotMatch(html, /data-salvar/)
    assert.match(html, /<textarea[^>]*disabled/)
    m.vm.fecharEditor()
    assert.equal(m.vm.editando.value, null)
    semSurpresa(m)
  }

  // 6. O registro: os filtros viram parâmetros; "só DaVinci" da linha filtra; "carregar mais" pagina; a conversa abre na Caixa.
  {
    const m = montar({
      rotas: {
        [GET_LISTA()]: cenario(),
        [GET_REG()]: REGISTRO,
        [GET_REG('automacao=shopee_menu&integration_id=i-kfa&so=so_davinci&limite=200')]: { linhas: [linhaReg({ id: 'l-9', duoke: 'nao_mandou' })], proximo: null },
        [GET_REG(`limite=200&antes=${encodeURIComponent(REGISTRO.proximo)}`)]: { linhas: [linhaReg({ id: 'l-1' }), linhaReg({ id: 'l-5' })], proximo: null },
        [GET_REG('duoke=pendente&limite=200')]: { linhas: [], proximo: null },
        [GET_REG('automacao=shopee_menu&limite=200')]: { linhas: [], proximo: null },
        [GET_LISTA('tiktok')]: resposta({ automacoes: [aut({ codigo: 'tiktok_convite', plataforma: 'tiktok', lojas: [loja()] })] }),
      },
    })
    await tique()
    await m.vm.carregarMaisRegistro()
    assert.deepEqual(m.vm.registro.value.map((l) => l.id), ['l-1', 'l-2', 'l-3', 'l-4', 'l-5'], 'sem repetir')
    assert.equal(m.vm.registroProximo.value, null)
    const a = m.vm.automacoes.value[0]
    m.vm.verNoRegistro(a, a.lojas[1], 'so_davinci')
    await tique()
    await tique()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/automacoes/registro?automacao=shopee_menu&integration_id=i-kfa&so=so_davinci&limite=200')
    assert.deepEqual(m.vm.registro.value.map((l) => l.id), ['l-9'])
    m.vm.limparFiltrosRegistro()
    await tique()
    m.vm.filtrosRegistro.comparacao = 'duoke:pendente'
    await tique()
    await tique()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/automacoes/registro?duoke=pendente&limite=200')
    let html = await render(m)
    assert.match(html, /data-registro-vazio[^>]*>\s*Nada no registro com esses filtros\.\s*</)
    assert.match(html, /limpar filtros/)
    m.vm.abrirConversa('c-1')
    m.vm.abrirConversa(null)
    // Buscar loja abre todas as seções; dá para fechar uma mesmo buscando.
    assert.deepEqual(m.vm.abertas.value, [])
    m.vm.busca.value = 'kf'
    await tique()
    assert.deepEqual(m.vm.abertas.value, ['shopee_menu', 'shopee_opcao_4', 'shopee_entregue'])
    m.vm.alternarAutomacao('shopee_opcao_4')
    assert.equal(m.vm.estaAberta('shopee_opcao_4'), false)
    assert.deepEqual(m.vm.lojasDe(m.vm.automacoes.value[0]).map((l) => l.loja), ['KFA'])
    m.vm.busca.value = ''
    await tique()
    assert.deepEqual(m.emitidos, [['abrir-conversa', 'c-1']])
    // Outra plataforma: relê a lista; o filtro de automação/loja (de outra plataforma) sai.
    m.vm.limparFiltrosRegistro()
    await tique()
    m.vm.filtrosRegistro.automacao = 'shopee_menu'
    await tique()
    m.vm.trocarPlataforma('tiktok')
    await tique()
    await tique()
    assert.ok(m.chamadas.some((c) => c.url === '/api/atendimento/automacoes?plataforma=tiktok&dias=7'))
    assert.equal(m.vm.filtrosRegistro.automacao, '')
    assert.deepEqual(m.vm.automacoes.value.map((x) => x.codigo), ['tiktok_convite'])
    html = await render(m)
    assert.doesNotMatch(html, /data-chave="auto_reply"/, 'o auto_reply é só da Shopee')
    semSurpresa(m)
  }

  // 7. Carregando, erro, vazio; o registro vazio diz por quê.
  {
    let soltar
    const m = montar({ rotas: { [GET_LISTA()]: () => new Promise((r) => { soltar = r }), [GET_REG()]: { linhas: [], proximo: null } } })
    await tique()
    let html = await render(m)
    assert.match(html, /data-carregando[\s\S]*carregando as automáticas…/)
    assert.match(html, /aria-busy="true"/)
    soltar(resposta({ chaves: { ...CHAVES, motor_ativo: false }, faixa: { nivel: 'aviso', texto: 'Motor desligado (ATENDIMENTO_AUTOMACOES_ATIVA): nada é simulado nem enviado.' }, automacoes: [aut({ lojas: [] })] }))
    await tique()
    html = await render(m)
    assert.match(html, /data-empty[^>]*>Nenhuma loja Shopee na sua equipe — /)
    assert.match(html, /data-faixa-automaticas[^>]*data-nivel="aviso"/)
    assert.match(html, /border-amber-500\/40 bg-amber-500\/10/)
    assert.match(html, /data-registro-vazio[^>]*>\s*Nada no registro ainda: o motor está desligado no servidor \(ATENDIMENTO_AUTOMACOES_ATIVA\)/)

    const m2 = montar({ rotas: { [GET_LISTA()]: () => { throw Object.assign(new Error('x'), { statusCode: 500 }) }, [GET_REG()]: () => { throw Object.assign(new Error('x'), { statusCode: 403 }) } } })
    await tique()
    html = await render(m2)
    assert.match(html, /data-erro[^>]*>\s*Não consegui carregar as automáticas\s*<button[^>]*>tentar de novo</)
    assert.match(html, /Sem permissão para isso\./, 'o erro do registro')
    // Erro na atualização automática não apaga o que está na tela.
    const rotas3 = { [GET_LISTA()]: cenario(), [GET_REG()]: { linhas: [], proximo: null } }
    const m3 = montar({ rotas: rotas3 })
    await tique()
    rotas3[GET_LISTA()] = () => { throw Object.assign(new Error('rede'), { statusCode: 502 }) }
    await m3.polls[0]()
    assert.equal(m3.vm.erro.value, null, 'no automático, sem erro na tela enquanto há dados')
    assert.equal(m3.vm.automacoes.value.length, 3)
    assert.equal(m3.vm.carregando.value, false)
    // O "atualizar" (não silencioso) mostra o erro, sem apagar a lista.
    m3.vm.atualizar()
    await tique()
    assert.equal(m3.vm.erro.value, 'Não consegui carregar as automáticas')
    assert.equal(m3.vm.automacoes.value.length, 3)
    html = await render(m3)
    assert.match(html, /role="alert">\s*Não consegui carregar as automáticas — mostrando o que já estava na tela\.\s*</)
    assert.match(html, /data-automacao="shopee_menu"/)
    semSurpresa(m)
    semSurpresa(m3)
  }

  // 9. A troca é LOJA por loja: o selo de cada loja, a conta da automação e o
  //    "sei que o critério não passou" no quadro do Enviar (e quando a API recusa).
  {
    const corpos = []
    let respostaKfa = 'criterio'
    const srv = servidor()
    // Barbosa: a API deixa pôr em Enviar, mas o critério da loja não passou.
    srv.gravar('shopee_menu', 'i-barbosa', { pode_enviar: true, por_que_nao_enviar: [] })
    const m = montar({
      rotas: {
        [GET_LISTA()]: srv.lista, [GET_REG()]: { linhas: [], proximo: null },
        [PATCH('shopee_menu', 'i-barbosa')]: (opts) => {
          corpos.push(['barbosa', opts.body])
          return { integration_id: 'i-barbosa', automacao: 'shopee_menu', regra: regra({ modo: 'enviar', enviar_desde: ISO }), rearmadas: 0, pode_enviar: true, por_que_nao_enviar: [] }
        },
        [PATCH('shopee_menu', 'i-kfa')]: (opts) => {
          corpos.push(['kfa', opts.body])
          if (respostaKfa === 'criterio') throw Object.assign(new Error('422'), { statusCode: 422, data: { detail: { code: 'criterio_nao_passou', motivos: ['"só DaVinci" nos últimos 2 dias'], detail: 'x' } } })
          return { integration_id: 'i-kfa', automacao: 'shopee_menu', regra: regra({ modo: 'enviar', enviar_desde: ISO }), rearmadas: 0, pode_enviar: true, por_que_nao_enviar: [] }
        },
      },
    })
    await tique()
    m.vm.alternarAutomacao('shopee_menu')
    let html = await render(m)
    // A automação só conta as lojas ligadas prontas (Barbosa, KFA, Mini: só a KFA).
    assert.match(html, /data-troca-automacao[^>]*>1 de 3 lojas prontas para trocar</)
    const seloDe = (id) => {
      const i = html.indexOf(`data-loja="${id}"`)
      const trecho = html.slice(i, html.indexOf('data-modo', i))
      return (trecho.match(/title="([^"]*)"[^>]*data-troca-loja[^>]*>([^<]*)</) || []).slice(1)
    }
    assert.deepEqual(seloDe('i-kfa')[1], 'pronta para trocar')
    assert.equal(seloDe('i-barbosa')[1], 'troca: ainda não')
    assert.match(seloDe('i-barbosa')[0], /poucos casos \(25 de 30\); mandaria para quem devolveu/)
    assert.deepEqual(seloDe('i-mini'), [], 'em Enviar não tem selo de troca')
    const a = m.vm.automacoes.value[0]
    // Barbosa: o quadro pede o "sei disso", e sem ele nada sai.
    m.vm.aoEscolherModo(a, a.lojas[0], { target: { value: 'enviar' } })
    html = await render(m)
    const quadro = html.slice(html.indexOf('data-pedir-enviar'))
    assert.match(quadro, /data-criterio-nao-passou[\s\S]*poucos casos \(25 de 30\)[\s\S]*Sei que o critério não passou nesta loja e quero trocar mesmo assim/)
    m.vm.desligueiNoDuoke.value = true
    await m.vm.confirmarEnviar(a, a.lojas[0])
    assert.deepEqual(corpos, [], 'sem o "sei disso", nada')
    m.vm.trocaSemCriterio.value = true
    await m.vm.confirmarEnviar(a, a.lojas[0])
    assert.deepEqual(corpos, [['barbosa', { modo: 'enviar', desliguei_no_duoke: true, troca_sem_criterio: true }]])
    assert.equal(m.vm.pedindoEnviar.value, null)
    // KFA: pronta na lista (sem o "sei disso"), mas a API recusa pelo critério → o quadro pede.
    m.vm.aoEscolherModo(a, a.lojas[1], { target: { value: 'enviar' } })
    assert.equal(m.vm.trocaSemCriterio.value, false, 'o quadro novo começa sem marcação')
    m.vm.desligueiNoDuoke.value = true
    await m.vm.confirmarEnviar(a, a.lojas[1])
    assert.deepEqual(corpos.at(-1), ['kfa', { modo: 'enviar', desliguei_no_duoke: true }])
    assert.equal(m.avisos.at(-1)[1], A.ERROS_AUTOMACAO.criterio_nao_passou)
    assert.deepEqual(m.avisos.at(-1)[2], ['"só DaVinci" nos últimos 2 dias'])
    assert.equal(m.vm.criterioFalhou.value, 'shopee_menu:i-kfa')
    html = await render(m)
    assert.match(html.slice(html.indexOf('data-pedir-enviar')), /data-criterio-nao-passou/)
    respostaKfa = 'ok'
    await m.vm.confirmarEnviar(a, a.lojas[1])
    assert.equal(corpos.length, 2, 'sem o "sei disso", não manda de novo')
    m.vm.trocaSemCriterio.value = true
    await m.vm.confirmarEnviar(a, a.lojas[1])
    assert.deepEqual(corpos.at(-1), ['kfa', { modo: 'enviar', desliguei_no_duoke: true, troca_sem_criterio: true }])
    assert.equal(m.vm.pedindoEnviar.value, null)
    // Opção: o quadro mostra quantas o DaVinci responderia a mais que o Duoke.
    const opcao = aut({ codigo: 'shopee_opcao_6', nome: 'Resposta da opção 6', tipo: 'opcao', gatilho: 'opcao', lojas: [loja({ pode_enviar: true, por_que_nao_enviar: [], pode_trocar: true, por_que_nao_trocar: [], periodo: conta({ simulado: 58, so_davinci: 33, cobertura: 0.99, casos: 60 }) })] })
    const m2 = montar({ rotas: { [GET_LISTA()]: resposta({ automacoes: [opcao] }), [GET_REG()]: { linhas: [], proximo: null } } })
    await tique()
    const o = m2.vm.automacoes.value[0]
    m2.vm.alternarAutomacao('shopee_opcao_6')
    m2.vm.aoEscolherModo(o, o.lojas[0], { target: { value: 'enviar' } })
    html = await render(m2)
    assert.match(html, /data-sobra-opcao[^>]*>\s*Nas opções o DaVinci responde na hora: em 7 dias ele teria respondido 33 vezes em que o Duoke não respondeu/)
    assert.doesNotMatch(html, /data-criterio-nao-passou/, 'a loja pronta não pede o "sei disso"')
    semSurpresa(m)
    semSurpresa(m2)
  }

  // 8. Plataforma e período lembrados neste navegador (sem armazenamento: o padrão).
  {
    const guardado = armazenamentoFalso({ 'davinci.atendimento.automaticas': JSON.stringify({ plataforma: 'ml', dias: 15 }) })
    const m = montar({ rotas: { [GET_LISTA('ml', 15)]: resposta(), [GET_REG()]: { linhas: [], proximo: null }, [GET_LISTA('ml', 30)]: resposta() }, armazenamento: guardado })
    await tique()
    assert.equal(m.chamadas[0].url, '/api/atendimento/automacoes?plataforma=ml&dias=15')
    m.vm.trocarDias(30)
    await tique()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/automacoes?plataforma=ml&dias=30')
    assert.deepEqual(JSON.parse(guardado.mapa.get('davinci.atendimento.automaticas')), { plataforma: 'ml', dias: 30 })
    const lixo = montar({ rotas: { [GET_LISTA()]: resposta(), [GET_REG()]: { linhas: [], proximo: null } }, armazenamento: armazenamentoFalso({ 'davinci.atendimento.automaticas': '{"plataforma":"amazon","dias":999' }) })
    await tique()
    assert.equal(lixo.chamadas[0].url, '/api/atendimento/automacoes?plataforma=shopee&dias=7')
    const quebrado = montar({ rotas: { [GET_LISTA()]: resposta(), [GET_REG()]: { linhas: [], proximo: null }, [GET_LISTA('tiktok')]: resposta() }, armazenamento: { getItem() { throw new Error('bloqueado') }, setItem() { throw new Error('bloqueado') } } })
    await tique()
    assert.equal(quebrado.chamadas[0].url, '/api/atendimento/automacoes?plataforma=shopee&dias=7')
    quebrado.vm.trocarPlataforma('tiktok')
    await tique()
    assert.equal(quebrado.chamadas.at(-1).url, '/api/atendimento/automacoes?plataforma=tiktok&dias=7', 'sem armazenamento, só não lembra')
    for (const x of [m, lixo, quebrado]) semSurpresa(x)
  }

}

// ------------------------------------------------ tema escuro e tela estreita
{
  // Toda cor de texto forte tem a versão do escuro na mesma lista de classes.
  const classes = [...autSfc.fonte.matchAll(/(['"`])([^'"`\n]*\b(?:text|bg|border)-(?:red|amber|emerald|sky|violet|orange)-[^'"`\n]*)\1/g)].map((m) => m[2])
  assert.ok(classes.length > 20)
  for (const c of classes) {
    if (/\btext-(?:red|amber|emerald|sky|violet|orange)-[6-9]00\b/.test(c)) assert.match(c, /dark:text-/, `tema escuro: "${c}"`)
  }
  assert.doesNotMatch(autSfc.fonte, /\b(?:bg-white|text-black|bg-gray-\d|text-gray-\d)\b/, 'cores pelos tokens do tema')
  assert.match(tela, /<div class="space-y-4 dark:\[color-scheme:dark\]" data-automaticas>/, 'os controles nativos (hora, número) no escuro')
  // Tela estreita: as linhas empilham; a grade de colunas só a partir do md.
  assert.match(A.GRADE_LOJA, /^md:grid-cols-\[/)
  assert.match(A.GRADE_REGISTRO, /^md:grid-cols-\[/)
  assert.match(tela, /class="hidden gap-x-3 bg-muted\/40 px-3 py-1\.5 text-\[11px\] font-semibold text-muted-foreground md:grid" :class="GRADE_LOJA"/)
  assert.match(tela, /class="grid grid-cols-3 items-center[^"]*" :class="GRADE_LOJA"/)
  assert.match(tela, /class="grid grid-cols-2 items-start[^"]*" :class="GRADE_REGISTRO"/)
  assert.match(tela, /<span class="md:hidden">\{\{ canEdit \? 'editar' : 'ver' \}\}<\/span>/)
  assert.ok((tela.match(/md:hidden/g) || []).length >= 6, 'os rótulos dos números aparecem no celular')
  assert.match(tela, /class="relative w-full sm:w-52"/)
}

// ------------------------------------------------ a página e a conversa
{
  const pagina = web('pages/atendimento.vue')
  assert.match(pagina, /type Aba = 'caixa' \| 'lojas' \| 'manual' \| 'modelos' \| 'automaticas' \| 'metricas'/)
  const abas = [...(pagina.match(/const ABAS[\s\S]*?\n\]/) || [''])[0].matchAll(/value: '([a-z]+)', label: '([^']+)', icon: (\w+)/g)].map((m) => [m[1], m[2], m[3]])
  assert.deepEqual(abas.map((a) => a[0]), ['caixa', 'lojas', 'manual', 'modelos', 'automaticas', 'metricas'], 'entre "Respostas prontas" e "Métricas"')
  assert.deepEqual(abas[4], ['automaticas', 'Automáticas', 'Bot'])
  assert.match(pagina, /import \{[^}]*\bBot\b[^}]*\} from 'lucide-vue-next'/)
  // `?tab=automaticas` abre direto (a regra geral das abas).
  assert.match(pagina, /const aba = ref<Aba>\(ABAS\.some\(\(a\) => a\.value === abaQuery\) \? \(abaQuery as Aba\) : 'caixa'\)/)
  assert.match(pagina, /<AtendimentoAutomaticas v-if="aba === 'automaticas'" :can-edit="canEdit" @abrir-conversa="abrirConversaNaCaixa" \/>/)
  assert.match(pagina, /function abrirConversaNaCaixa\(id: string\) \{\n {2}aba\.value = 'caixa'\n {2}selecionar\(id\)\n\}/)
  // A mesma trava da página (admin + ATENDIMENTO_USUARIOS): a aba não abre nada novo.
  assert.match(pagina, /definePageMeta\(\{ middleware: \['admin', 'atendimento'\] \}\)/)

  // A conversa: a mensagem automática do DaVinci tem nome, cor e explicação;
  // o "a conferir" vale para ela (o balão não filtra a origem) e o "tentar de
  // novo" à mão, não (ela é da regra, não de uma pessoa).
  assert.equal(P.origemLabel({ autor: 'loja', origem: 'davinci_auto', autor_nome: null }, 'shopee'), 'Automática · DaVinci')
  assert.equal(P.origemLabel({ autor: 'loja', origem: 'davinci_auto', autor_nome: 'Ana' }, 'ml'), 'Automática · DaVinci')
  assert.equal(P.origemLabel({ autor: 'loja', origem: 'davinci_ia', autor_nome: null }, 'shopee'), 'IA')
  const conversa = sfc('../components/AtendimentoConversa.vue')
  const cs = conversa.descriptor.scriptSetup.content
  assert.match(cs, /if \(m\.origem === 'davinci_auto'\) return 'text-sky-700 dark:text-sky-300'/)
  assert.match(cs, /davinci_auto: 'mensagem automática do DaVinci \(regra da loja na aba Automáticas\)',/)
  assert.match(cs, /: ultima\.origem === 'davinci_auto'\n\s+\? 'A mensagem automática do DaVinci'/)
  assert.match(conversa.descriptor.template.content, /v-if="l\.m\.status === 'revisar' && lado\(l\.m\) === 'loja' && !l\.de"/)
  assert.match(cs, /if \(m\.origem !== 'davinci_humano' && m\.origem !== 'davinci_ia'\) return false/)
}

principal().then(() => console.log('ok atendimento-automaticas')).catch((e) => {
  console.error(e)
  process.exit(1)
})
