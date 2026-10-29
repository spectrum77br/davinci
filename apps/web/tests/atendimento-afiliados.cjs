// node tests/atendimento-afiliados.cjs — o chat de AFILIADO da Shopee na Caixa (29/10/2026).
// O dono: "afiliados não tá dando pra ver no comunicador e no duoke dá, queria
// que desse pra ver ali também". O chat de afiliado (o criador de conteúdo que
// divulga a loja, `dados.afiliado` desde 15/10/2026) já vinha na lista em
// "Todas", perdido entre os outros e sem resposta (o selo dizia "a resposta é
// pelo Duoke"). Agora:
//  - o menu Filtrar tem "Afiliados" (`filtro=afiliado`, os abertos — como o
//    selo "parceiro" do Duoke), com o número do /resumo (`afiliados`) no
//    total, na plataforma e na loja; fora da Caixa Humano (a IA nunca o pega);
//  - o selo da linha diz "Afiliado" e que a pessoa responde por aqui;
//  - a conversa aberta não oferece "Sugerir" (a IA nunca entra) e a caixa de
//    resposta é a de sempre (o servidor libera só a pessoa);
//  - e o contrato com a API.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

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
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return src.slice(a, b)
}
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const listaSfc = sfc('../components/AtendimentoLista.vue')
const L = exportsDe(listaSfc)
const lista = web('components/AtendimentoLista.vue')

// ------------------------------------------------ o menu Filtrar
{
  const af = L.FILTROS_MENU.find((f) => f.value === 'afiliado')
  assert.ok(af, 'o filtro Afiliados no menu')
  assert.equal(af.label, 'Afiliados')
  assert.match(af.hint, /afiliado da Shopee/)
  assert.match(af.hint, /responda por aqui/)
  assert.ok(!L.FILTROS_ETIQUETA.has('afiliado'), 'não é etiqueta: fica antes do separador "Etiqueta"')
  const valores = L.FILTROS_MENU.map((f) => f.value)
  assert.ok(valores.indexOf('afiliado') < valores.indexOf('reclamacao'), 'antes das etiquetas')
  assert.ok(L.FILTROS_RAPIDOS.some((f) => f.value === 'afiliado'))
  // Na Caixa Humano não aparece (a IA nunca pega o chat de afiliado); na Caixa, sim.
  assert.ok(L.opcoesDoFiltrar('').some((f) => f.value === 'afiliado'))
  assert.ok(!L.opcoesDoFiltrar('humano').some((f) => f.value === 'afiliado'))
  assert.ok(L.FILTROS_FORA_DA_HUMANO.has('afiliado'))
  // A lista vazia explica.
  assert.match(lista, /afiliado: 'Nenhum chat de afiliado aberto com esses filtros\.'/)
}

// ------------------------------------------------ o número do filtro (do /resumo)
{
  const setup = listaSfc.scriptSetup.content
  const corpo = transpile(trecho(setup, 'function porEtiqueta', 'function contadorCls'))
  const montar = (props, f) => new Function(
    'props', 'filtros', 'computed', 'FILTROS_ETIQUETA',
    `${corpo}\nreturn { contagem, afiliadosDe }`,
  )(props, { value: f }, (fn) => ({ get value() { return fn() } }), L.FILTROS_ETIQUETA)
  const filtros = (o = {}) => ({ plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', ...o })
  const RESUMO = {
    plataformas: [
      { plataforma: 'shopee', aguardando: 3, vencendo: 0, vencidas: 0, afiliados: 2, etiquetas: {} },
      { plataforma: 'ml', aguardando: 1, vencendo: 0, vencidas: 0, afiliados: 0, etiquetas: {} },
    ],
    lojas: [
      { integration_id: 'i-mega', plataforma: 'shopee', conta: 'mega', aguardando: 2, vencidas: 0, afiliados: 2, etiquetas: {} },
      { integration_id: 'i-atv', plataforma: 'shopee', conta: 'ATV', aguardando: 1, vencidas: 0, afiliados: 0, etiquetas: {} },
    ],
    canais: [],
    flags: {},
    etiquetas: {},
    afiliados: 2,
  }
  assert.equal(montar({ resumo: RESUMO }, filtros()).contagem.value.afiliado, 2, 'o total do topo')
  assert.equal(montar({ resumo: RESUMO }, filtros({ plataforma: 'shopee' })).contagem.value.afiliado, 2, 'a plataforma')
  assert.equal(montar({ resumo: RESUMO }, filtros({ plataforma: 'ml' })).contagem.value.afiliado, 0, 'o ML não tem afiliado')
  assert.equal(montar({ resumo: RESUMO }, filtros({ integration_id: 'i-mega' })).contagem.value.afiliado, 2, 'a loja')
  assert.equal(montar({ resumo: RESUMO }, filtros({ integration_id: 'i-atv' })).contagem.value.afiliado, 0)
  // A API antiga (sem o campo): sem número, nunca um zero que mente.
  const semCampo = (o) => {
    const { afiliados: _, ...resto } = o
    return resto
  }
  const ANTIGO = { ...semCampo(RESUMO), plataformas: RESUMO.plataformas.map(semCampo), lojas: RESUMO.lojas.map(semCampo) }
  assert.equal(montar({ resumo: ANTIGO }, filtros()).contagem.value.afiliado, null)
  assert.equal(montar({ resumo: ANTIGO }, filtros({ plataforma: 'shopee' })).contagem.value.afiliado, null)
  assert.equal(montar({ resumo: ANTIGO }, filtros({ integration_id: 'i-mega' })).contagem.value.afiliado, null)
  const { afiliadosDe } = montar({ resumo: RESUMO }, filtros())
  assert.equal(afiliadosDe([]), 0, 'a plataforma sem conversa conta zero')
  assert.equal(afiliadosDe([{ afiliados: 1 }, { afiliados: 3 }]), 4)
  assert.equal(afiliadosDe([{ afiliados: 1 }, {}]), null)
  // A cor do número é a do selo (âmbar).
  assert.match(trecho(setup, 'function contadorCls', '// ─── menu "Filtrar"'), /value === 'afiliado'\) return 'bg-amber-500\/15/)
}

// ------------------------------------------------ o selo da linha
{
  const tpl = listaSfc.template.content
  const selo = trecho(tpl, 'v-if="c.afiliado"', '</span>')
  assert.match(selo, /data-selo-afiliado/)
  assert.match(selo, />Afiliado$/, 'o selo diz "Afiliado"')
  assert.match(selo, /responda por aqui/)
  assert.doesNotMatch(selo, /pelo Duoke/, 'o selo não manda mais responder no Duoke')
  assert.match(lista, /function temSelos[\s\S]{0,160}c\.afiliado/, 'a linha com o selo abre a faixa de selos')
}

// ------------------------------------------------ a conversa aberta
{
  const conversa = web('components/AtendimentoConversa.vue')
  const pode = trecho(conversa, 'const podeSugerir = computed(', '})')
  assert.match(pode, /c\.canal !== 'zap' && !c\.afiliado/, 'sem "Sugerir" no chat de afiliado')
  const rotulo = trecho(conversa, 'data-msg-afiliado', '</div>')
  assert.ok(rotulo.length > 0)
  const div = trecho(conversa, "v-else-if=\"lado(l.m) === 'afiliado'\"", 'data-msg-afiliado')
  assert.match(div, /responda por aqui/)
  assert.doesNotMatch(div, /pelo Duoke/)
  // A resposta que a Shopee pôs noutro chat (`revisar`, erro do adaptador): o balão diz onde foi parar.
  const outro = trecho(conversa, "v-else-if=\"l.m.erro === 'shopee afiliado_outro_chat'\"", '</div>')
  assert.match(outro, /data-afiliado-outro-chat/)
  assert.match(outro, /chat de comprador/)
  assert.match(api('services/atendimento/shopee.py'), /ERRO_AFILIADO_OUTRO_CHAT = "shopee afiliado_outro_chat"/)
  const P = web('components/AtendimentoPlataforma.vue')
  assert.equal((P.match(/afiliados\?: number/g) || []).length, 3, 'o número no tipo da plataforma, da loja e do total')
}

// ------------------------------------------------ o contrato com a API
{
  const router = api('routers/atendimento.py')
  assert.match(router, /FILTROS = \([\s\S]*?"afiliado",\n\)/, 'o filtro na lista do servidor')
  assert.match(router, /elif filtro == FILTRO_AFILIADO:[\s\S]{0,200}gravar\.chat_de_afiliado_sql\(\)/)
  assert.match(router, /afiliados = func\.count\(\)\.filter\(aberta, gravar\.chat_de_afiliado_sql\(\)\)/)
  const schemas = api('schemas/atendimento.py')
  for (const classe of ['PlataformaResumoOut', 'LojaResumoOut', 'ResumoOut']) {
    assert.match(schemas, new RegExp(`class ${classe}\\(BaseModel\\):[\\s\\S]*?afiliados: int = 0`), classe)
  }
  const constantes = api('services/atendimento/constantes.py')
  assert.match(constantes, /return MOTIVO_AFILIADO_SO_PESSOA/, 'a IA e a automática continuam de fora')
  const enviar = api('services/atendimento/enviar.py')
  assert.match(enviar, /motivo_sem_envio == MOTIVO_AFILIADO_SO_PESSOA and origem == ORIGEM_HUMANO/, 'só a pessoa passa')
  const shopee = api('services/atendimento/shopee.py')
  assert.match(shopee, /def negocio_do_envio[\s\S]{0,600}"business_type": NEGOCIO_AFILIADO, "conversation_id": conversa\.externo_id/)
}

console.log('atendimento-afiliados: ok')
