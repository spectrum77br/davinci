// node tests/garantias-sfc.cjs — Pós-venda › Garantias (Painel de Garantia Uranyx, 07/10/2026).
// Documento "Painel de Garantia — Uranyx (DaVinci)": a tela do §4.1 (busca, status, período, os 4
// indicadores que filtram, a tabela com o CPF mascarado e o status 🟡🟢🔵🔴), o cadastro do §3
// (pedido → data inicial travada, fins calculados, NF/nome/CPF obrigatórios e CPF validado), o
// detalhe do §4.2 (barras de dias restantes, aba Atendimentos em linha do tempo, log para admin),
// e o "Vincular à garantia" do §5 no /atendimento (busca já preenchida, tipo HW/SW, prévia
// Coberto/Fora antes de salvar, mensagens e anexos da conversa, alerta de CPF sem garantia) — com
// as permissões do §6 (sem "Registrar atendimento" o botão não aparece; a trava de só leitura do
// Atendimento não bloqueia quem tem a permissão da garantia). O contrato é conferido contra
// apps/api/app/schemas/garantia.py e as regras contra apps/api/app/services/garantia.py.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (src, module = ts.ModuleKind.CommonJS) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module, esModuleInterop: true } }).outputText
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

function loadLib(rel, globais = {}, req = require) {
  const exp = {}
  const nomes = Object.keys(globais)
  new Function('exports', 'require', ...nomes, transpile(ler(rel)))(exp, req, ...nomes.map((n) => globais[n]))
  return exp
}
const L = loadLib('../lib/garantias.ts')
const P = (() => {
  const { descriptor } = parse(ler('../components/AtendimentoPlataforma.vue'))
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
})()

// ─── harness: o <script setup> de verdade, com as auto-importações falsas ────
const icone = { render: () => Vue.h('i') }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => icone })
  if (nome === '@vueuse/core') return { onKeyStroke: () => {}, useMediaQuery: () => Vue.ref(true) }
  if (nome === '~/lib/garantias') return L
  if (nome === '~/components/AtendimentoPlataforma.vue') return P
  return require(nome)
}
const cacheSfc = new Map()
function sfc(rel) {
  if (cacheSfc.has(rel)) return cacheSfc.get(rel)
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({
    source: descriptor.template.content, filename, id: path.basename(rel),
    compilerOptions: { isTS: true, bindingMetadata: script.bindings },
  })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  const render = {}
  new Function('exports', 'require', transpile(tpl.code))(render, require)
  const out = { descriptor, codigo: transpile(script.content), render: render.render }
  cacheSfc.set(rel, out)
  return out
}
const AUTO = ['ref', 'computed', 'reactive', 'watch', 'onMounted', 'nextTick', 'useApi', 'useToasts', 'useRoute', 'useRouter', 'useGarantiaAcesso', 'useAuthStore', 'definePageMeta', 'useCan']
function componente(rel, g) {
  const { codigo, render } = sfc(rel)
  const mod = { exports: {} }
  const valores = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: g.onMounted || (() => {}),
    useApi: () => ({ api: g.api || (async () => { throw new Error('api não esperada') }) }),
    useToasts: () => g.toasts || { success() {}, error() {}, info() {}, warning() {} },
    useRoute: () => g.route || { query: {} },
    useRouter: () => g.router || { replace: async () => {} },
    useGarantiaAcesso: () => Vue.computed(() => L.acessoGarantia(g.user)),
    useAuthStore: () => ({ user: g.user, isAdmin: g.user?.role === 'admin' }),
    definePageMeta: (m) => { g.meta = m },
    useCan: (r, a) => Vue.computed(() => g.user?.role === 'admin' || g.user?.permissions?.[r]?.[a] === true),
  }
  new Function('require', 'module', 'exports', ...AUTO, codigo)(requerer, mod, mod.exports, ...AUTO.map((n) => valores[n]))
  return { ...mod.exports.default, render }
}
// Roda o setup fora do Vue (para chamar as funções e ler o estado).
function montar(rel, props, g = {}) {
  const montados = []
  const emitidos = []
  const C = componente(rel, { ...g, onMounted: (fn) => montados.push(fn) })
  const reativo = Vue.reactive({ ...props })
  const estado = Vue.proxyRefs(C.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }))
  return { estado, emitidos, reativo, montar: async () => { for (const f of montados) await f() } }
}
const STUBS = {
  Button: { props: ['disabled', 'as', 'href', 'size', 'variant', 'type'], setup: (p, { slots, attrs }) => () => Vue.h('button', { ...attrs, disabled: p.disabled, type: p.type }, slots.default?.()) },
  Input: { props: ['modelValue'], setup: (p, { attrs }) => () => Vue.h('input', { ...attrs, value: p.modelValue }) },
  Label: { setup: (_p, { slots, attrs }) => () => Vue.h('label', attrs, slots.default?.()) },
  NuxtLink: { props: ['to'], setup: (p, { slots, attrs }) => () => Vue.h('a', { ...attrs, href: p.to }, slots.default?.()) },
  EmptyState: { props: ['title', 'description', 'icon'], setup: (p, { slots, attrs }) => () => Vue.h('div', { ...attrs, 'data-empty': '' }, [p.title, ' ', p.description, slots.default?.()]) },
  PageHeader: { props: ['title', 'description'], setup: (p, { slots }) => () => Vue.h('header', [Vue.h('h1', p.title), slots.actions?.()]) },
}
// Os componentes de garantia que a página/detalhe usam: vazios, a não ser os passados em `extras`.
const VAZIOS = ['GarantiaStatus', 'GarantiaCobertura', 'GarantiaPrazoBarra', 'GarantiaDetalhe', 'GarantiaForm', 'GarantiaVincular', 'AtendimentoGarantia']
function appCom(C, props, g = {}, extras = {}) {
  const app = Vue.createSSRApp(C, props)
  for (const [n, c] of Object.entries(STUBS)) app.component(n, c)
  for (const n of VAZIOS) app.component(n, extras[n] ? componente(extras[n], g) : { render: () => null })
  return app
}
const html = async (app) => (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
// O setup de verdade guardado (para carregar e renderizar de novo com o estado).
function comEstado(C) {
  const caixa = { estado: null }
  caixa.C = { ...C, setup(p, ctx) { caixa.estado = Vue.proxyRefs(C.setup(p, ctx)); return caixa.estado } }
  caixa.mesmo = { ...C, setup: () => caixa.estado }
  return caixa
}
async function renderizar(rel, props, g = {}, extras = {}) {
  return html(appCom(componente(rel, g), props, g, extras))
}
const texto = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()

const ADMIN = { id: 'a', role: 'admin', permissions: {} }
const CONSULTA = { id: 'c', role: 'user', permissions: { garantias: { view: true } } }
const CADASTRA = { id: 'k', role: 'user', permissions: { garantias: { view: true, edit: true } } }
const REGISTRA = { id: 'r', role: 'user', permissions: { garantias_atendimento: { view: true, edit: true } } }
const COMUM = { id: 'u', role: 'user', permissions: {} }

const LINHA = {
  id: 7, cliente_nome: 'João Silva', cpf_mascarado: '***.982.247-**', nf_numero: '10234', nf_serie: '1',
  pedido_bling: '55001', pedido_marketplace: '2000012345', plataforma: 'ml', conta: 'Loja X',
  data_inicio: '2026-10-07', fim_hardware: '2027-01-07', fim_software: '2027-10-07',
  status: 'ativa', status_rotulo: 'Ativa', entregue_sem_data: false, atendimentos: 2, criado_em: '2026-10-07T15:00:00Z',
}

// ═══════════════════════════════════════════════════════════════ lib
{
  // CPF (RN06): máscara, dígito verificador, repetidos, letra.
  assert.equal(L.formatarCpf('52998224725'), '529.982.247-25')
  assert.equal(L.formatarCpf('529.982'), '529.982')
  assert.equal(L.formatarCpf('5299822'), '529.982.2')
  assert.equal(L.formatarCpf('529982247251234'), '529.982.247-25', 'corta em 11 dígitos')
  assert.equal(L.cpfValido('529.982.247-25'), true)
  assert.equal(L.cpfValido('52998224725'), true)
  assert.equal(L.cpfValido('529.982.247-24'), false, 'dígito errado')
  assert.equal(L.cpfValido('111.111.111-11'), false, 'repetido não existe')
  assert.equal(L.cpfValido('5299822472a'), false, 'letra')
  assert.equal(L.cpfValido('5299822472'), false, 'incompleto')
  assert.equal(L.erroDoCpf(''), 'Informe o CPF.')
  assert.match(L.erroDoCpf('529.982'), /incompleto/)
  assert.match(L.erroDoCpf('529.982.247-24'), /inválido/)
  assert.equal(L.erroDoCpf('529.982.247-25'), '')
  // §6: a lista mostra ***.456.789-**
  assert.equal(L.mascararCpf('12345678901'), '***.456.789-**')
  assert.equal(L.mascararCpf('123'), null)

  // Datas no fuso de São Paulo (a tabela do §4.1 usa dd/mm/aa).
  assert.equal(L.dataCurta('2026-10-07'), '07/10/26')
  assert.equal(L.dataBR('2027-02-28'), '28/02/2027')
  assert.equal(L.dataBR(null), '—')
  assert.equal(L.diaEmSP('2026-10-08T02:30:00Z'), '2026-10-07', '23h30 em SP ainda é dia 7')
  assert.equal(L.diaEmSP('2026-10-08T03:30:00Z'), '2026-10-08')

  // Ponto 1: até a data final, inclusive (e o contrário, se o dono trocar).
  assert.equal(L.coberto('2027-01-07', '2027-01-07'), true)
  assert.equal(L.coberto('2027-01-07', '2027-01-08'), false)
  assert.equal(L.coberto('2027-01-07', '2027-01-07', false), false)
  assert.equal(L.coberto(null, '2026-01-01'), false)

  // §5.2: Coberto se a data do atendimento ≤ fim do TIPO informado.
  const g = { data_inicio: '2026-10-07', fim_hardware: '2027-01-07', fim_software: '2027-10-07' }
  assert.deepEqual(L.previaCobertura(g, 'hardware', '2027-01-07T20:00:00Z'), { cobertura: 'coberto', fim: '2027-01-07', dia: '2027-01-07' })
  assert.equal(L.previaCobertura(g, 'hardware', '2027-01-08T12:00:00Z').cobertura, 'fora_da_garantia')
  assert.equal(L.previaCobertura(g, 'software', '2027-01-08T12:00:00Z').cobertura, 'coberto', 'o software vai até 12 meses')
  assert.equal(L.previaCobertura(g, 'hardware', '2027-01-08T02:00:00Z').cobertura, 'coberto', '23h do dia 7 em SP')
  assert.equal(L.previaCobertura({ data_inicio: null, fim_hardware: null, fim_software: null }, 'hardware', '2026-10-07T12:00:00Z').cobertura, 'sem_data_de_entrega')

  // Data do atendimento: o relógio da plataforma, como o backend.
  const msgs = [
    { id: 'm1', autor: 'cliente', enviada_em: '2026-10-01T10:00:00Z', anexos: [{ tipo: 'imagem', url: 'https://cf.shopee.com.br/a.jpg' }] },
    { id: 'm2', autor: 'loja', enviada_em: '2026-10-01T11:00:00Z', anexos: [{ tipo: 'pedido', numero: '1' }] },
    { id: 'm3', autor: 'cliente', enviada_em: '2026-10-03T09:00:00Z', anexos: [{ tipo: 'arquivo', nome: 'nota.pdf' }] },
    { id: 's1', autor: 'sistema', enviada_em: '2026-10-04T09:00:00Z', anexos: [] },
  ]
  assert.equal(L.dataDoAtendimento(msgs, [], null), '2026-10-01T10:00:00Z', 'sem escolha: a 1ª do cliente no trecho atual (a loja respondeu, o cliente voltou em 2 dias)')
  // O trecho atual: pausa > 7 dias abre trecho novo; a reclamação dentro do prazo não vira "Fora"
  // porque o vídeo chegou depois do fim (revisão 07/10/2026).
  const trecho = [
    { id: 'a', autor: 'cliente', enviada_em: '2026-07-07T12:00:00Z' },
    { id: 'b', autor: 'loja', enviada_em: '2026-07-07T13:00:00Z' },
    { id: 'c', autor: 'cliente', enviada_em: '2026-10-05T12:00:00Z' },
    { id: 'd', autor: 'mediador', origem: 'sistema', enviada_em: '2026-10-06T09:00:00Z' },
    { id: 'e', autor: 'cliente', enviada_em: '2026-10-08T12:00:00Z' },
  ]
  assert.equal(L.dataDoAtendimento(trecho, [], null), '2026-10-05T12:00:00Z')
  assert.equal(L.previaCobertura({ data_inicio: '2026-07-06', fim_hardware: '2026-10-06', fim_software: '2027-07-06' }, 'hardware', L.dataDoAtendimento(trecho, [], null)).cobertura, 'coberto')
  assert.equal(L.inicioDoTrecho([{ id: 'x', autor: 'cliente', enviada_em: '2026-09-01T12:00:00Z' }, { id: 'y', autor: 'cliente', enviada_em: '2026-09-08T12:00:00Z' }]), '2026-09-01T12:00:00Z', '7 dias exatos: o mesmo trecho')
  assert.equal(L.inicioDoTrecho([{ id: 'x', autor: 'cliente', enviada_em: '2026-09-01T12:00:00Z' }, { id: 'y', autor: 'cliente', enviada_em: '2026-09-08T12:01:00Z' }]), '2026-09-08T12:01:00Z')
  assert.equal(L.inicioDoTrecho([{ id: 'x', autor: 'cliente', enviada_em: '2026-09-01T12:00:00Z' }, { id: 'y', autor: 'cliente', enviada_em: '2026-09-08T12:01:00Z' }], 30), '2026-09-01T12:00:00Z', 'a pausa vem do GET /regras')
  assert.equal(L.inicioDoTrecho([{ id: 'x', autor: 'loja', enviada_em: '2026-09-01T12:00:00Z' }]), null)
  assert.deepEqual(L.mensagensDoVinculo(trecho, []).map((m) => m.id), ['a', 'b', 'c', 'd', 'e'], 'o mediador vai na cópia (só autor sistema sai)')
  assert.equal(L.dataDoAtendimento(msgs, ['m3', 'm1', 'm2'], null), '2026-10-01T10:00:00Z', 'escolhidas: a 1ª do cliente')
  assert.equal(L.dataDoAtendimento(msgs, ['m2'], null), '2026-10-01T11:00:00Z', 'sem cliente entre as escolhidas: a 1ª escolhida')
  assert.equal(L.dataDoAtendimento([], [], '2026-09-30T08:00:00Z'), '2026-09-30T08:00:00Z')
  assert.deepEqual(L.mensagensDoVinculo(msgs, []).map((m) => m.id), ['m1', 'm2', 'm3'], 'sem escolha: sem as de sistema')
  assert.deepEqual(L.mensagensDoVinculo(msgs, ['s1']).map((m) => m.id), ['s1'], 'escolhida vai mesmo sendo de sistema')
  // Anexos: sem os cartões de pedido/produto; ML fica só com o nome.
  assert.deepEqual(L.anexosDasMensagens(msgs), [
    { mensagem_id: 'm1', tipo: 'imagem', url: 'https://cf.shopee.com.br/a.jpg', nome: null },
    { mensagem_id: 'm3', tipo: 'arquivo', url: null, nome: 'nota.pdf' },
  ])
  assert.equal(L.ehImagem({ tipo: 'imagem' }), true)
  assert.equal(L.ehImagem({ content_type: 'application/pdf', nome: 'x.pdf' }), false)

  // Barra de dias restantes (§4.2).
  assert.deepEqual(L.barraPrazo({ fim: '2027-01-07', dias_total: 92, dias_restantes: 92, coberto_hoje: true }), { pct: 100, texto: '92 dias restantes', nivel: 'folga' })
  assert.equal(L.barraPrazo({ fim: '2027-01-07', dias_total: 92, dias_restantes: 10, coberto_hoje: true }).nivel, 'perto', 'últimos 30 dias')
  assert.equal(L.barraPrazo({ fim: '2027-01-07', dias_total: 92, dias_restantes: 0, coberto_hoje: true }).texto, 'último dia hoje')
  assert.deepEqual(L.barraPrazo({ fim: '2027-01-07', dias_total: 92, dias_restantes: 0, coberto_hoje: false }), { pct: 0, texto: 'terminou em 07/01/2027', nivel: 'acabou' })
  assert.match(L.barraPrazo(null).texto, /aguardando/)

  // As classes de cor ficam nos componentes: o Tailwind não lê lib/ (o tema escuro sumia).
  assert.ok(!/(bg|text|border)-(emerald|amber|sky|red)-\d/.test(ler('../lib/garantias.ts')), 'nenhuma classe de cor em lib/')
  assert.ok(!/['"]\.\/lib\//.test(ler('../tailwind.config.ts')), '(lib/ fora do content do Tailwind — por isso as cores ficam nos componentes)')
  for (const c of ['GarantiaStatus', 'GarantiaCobertura']) assert.match(ler(`../components/${c}.vue`), /dark:bg-emerald-500\/15/, `${c}: cor do tema escuro`)
  // Status com a cor/emoji do documento.
  assert.deepEqual(Object.fromEntries(Object.entries(L.STATUS).map(([k, v]) => [k, [v.emoji, v.rotulo]])), {
    aguardando_entrega: ['🟡', 'Aguardando entrega'], ativa: ['🟢', 'Ativa'], somente_software: ['🔵', 'Somente software'], expirada: ['🔴', 'Expirada'],
  })
  // O "Aguardando" de pedido que o Bling já dá como entregue: outro texto, mesmo status.
  assert.equal(L.statusInfo('aguardando_entrega', true).rotulo, 'Entregue — sem data no DaVinci')
  assert.equal(L.statusInfo('aguardando_entrega').rotulo, 'Aguardando entrega')
  assert.equal(L.statusInfo('ativa', true).rotulo, 'Ativa', 'só vale para o aguardando')
  assert.deepEqual(L.FILTROS_STATUS.map((f) => f.value), ['todos', 'ativa', 'somente_software', 'expirada', 'aguardando_entrega', 'entregue_sem_data', 'hw_vence_30d'])

  // Lista: o corpo do POST /lista (a busca nunca vai na URL) e os cartões que filtram.
  const f0 = { ...L.FILTROS_PADRAO }
  assert.deepEqual(L.corpoDaLista(f0), { limite: 100, offset: 0 })
  assert.deepEqual(L.corpoDaLista({ ...f0, busca: ' João ', status: 'expirada', periodo: 'entrega', de: '2026-01-01', ate: '', com_atendimento_no_mes: true }, 100),
    { limite: 100, offset: 100, busca: 'João', status: 'expirada', periodo: 'entrega', de: '2026-01-01', com_atendimento_no_mes: true })
  const f1 = L.filtroDoCartao(f0, 'ativas')
  assert.equal(f1.status, 'ativa')
  assert.equal(L.cartaoAtivo(f1), 'ativas')
  assert.equal(L.filtroDoCartao(f1, 'ativas').status, 'todos', 'clicar de novo tira o filtro')
  const f2 = L.filtroDoCartao(f1, 'atendimentos_no_mes')
  assert.deepEqual([f2.status, f2.com_atendimento_no_mes], ['todos', true])
  assert.deepEqual([L.filtroDoCartao(f2, 'hw_vence_30d').status, L.filtroDoCartao(f2, 'hw_vence_30d').com_atendimento_no_mes], ['hw_vence_30d', false])
  assert.deepEqual(L.CARTOES.map((c) => c.rotulo), ['Ativas', 'Só software', 'HW vence em 30d', 'Atendimentos no mês'], 'os 4 do §4.1')

  // Erros: por campo (os nossos e os do Pydantic), 409 com a garantia existente.
  assert.deepEqual(L.errosDaApi({ data: { detail: { code: 'cpf_invalido', campo: 'cpf' } } }).campos, { cpf: 'CPF inválido — confira os dígitos.' })
  const dup = L.errosDaApi({ statusCode: 409, data: { detail: { code: 'garantia_duplicada', campo: 'nf_numero', garantia_id: 3 } } })
  assert.deepEqual([dup.campos.nf_numero, dup.garantiaId], ['Já existe garantia para este CPF nesta NF.', 3])
  assert.equal(L.errosDaApi({ data: { detail: { code: 'atendimento_repetido', atendimento_id: 9 } } }).atendimentoId, 9)
  const extra = L.errosDaApi({ data: { detail: [{ loc: ['body', 'data_inicio'], msg: 'Extra inputs are not permitted' }] } })
  assert.match(extra.campos.data_inicio, /calculado pelo sistema/, 'RN01: data inicial não se manda')
  assert.equal(L.errosDaApi({ statusCode: 403 }).geral, 'Sem permissão para isso.')
  assert.match(L.errosDaApi({}).geral, /Sem conexão/)
  assert.match(L.errosDaApi({ statusCode: 502 }).geral, /erro 502/)
  assert.match(L.errosDaApi({ statusCode: 429, data: { detail: { code: 'muitas_buscas_por_cpf', retry_after: 125 } } }).geral, /Muitas buscas pelo CPF[\s\S]*de novo em 3 min/)

  // Formulário (§3): NF, nome e CPF obrigatórios; CPF inválido bloqueia.
  const vazio = { pedido: '', nf_numero: '', nf_serie: '', cliente_nome: '', cpf: '' }
  assert.deepEqual(Object.keys(L.validarFormulario(vazio)).sort(), ['cliente_nome', 'cpf', 'nf_numero', 'pedido'])
  const bom = { pedido: '55001', nf_numero: '000.010.234', nf_serie: '1', cliente_nome: '  João   Silva ', cpf: '529.982.247-25' }
  assert.deepEqual(L.validarFormulario(bom), {})
  assert.match(L.validarFormulario({ ...bom, cpf: '529.982.247-24' }).cpf, /inválido/)
  assert.match(L.validarFormulario({ ...bom, nf_numero: '12a' }).nf_numero, /inválida/)
  assert.match(L.validarFormulario({ ...bom, nf_numero: '1234567890' }).nf_numero, /9 dígitos/)
  assert.match(L.validarFormulario({ ...bom, nf_serie: '1234' }).nf_serie, /Série/)
  assert.deepEqual(L.validarFormulario({ ...bom, cpf: '' }, true), {}, 'na correção, CPF vazio mantém o atual')
  // O corpo do POST: só o informado — nada de data inicial, fins, status ou data de cadastro.
  assert.deepEqual(L.corpoDoCadastro(bom), { pedido: '55001', nf_numero: '000.010.234', nf_serie: '1', cliente_nome: 'João Silva', cpf: '52998224725' })
  assert.deepEqual(Object.keys(L.corpoDoCadastro({ ...bom, nf_serie: '' })).sort(), ['cliente_nome', 'cpf', 'nf_numero', 'pedido'])
  // A correção manda só o que mudou.
  const orig = { pedido_bling: '55001', pedido_marketplace: '2000012345', nf_numero: '10234', nf_serie: '1', cliente_nome: 'João Silva', cpf: '529.982.247-25', cpf_completo: true }
  assert.deepEqual(L.corpoDaCorrecao(orig, { pedido: '2000012345', nf_numero: '0010234', nf_serie: '1', cliente_nome: 'João Silva', cpf: '529.982.247-25' }), {}, 'nada mudou')
  assert.deepEqual(L.corpoDaCorrecao(orig, { pedido: '55001', nf_numero: '10235', nf_serie: '', cliente_nome: 'João da Silva', cpf: '' }),
    { nf_numero: '10235', nf_serie: '', cliente_nome: 'João da Silva' })
  assert.deepEqual(L.corpoDaCorrecao({ ...orig, cpf: '***.982.247-**', cpf_completo: false }, { pedido: '55002', nf_numero: '10234', nf_serie: '1', cliente_nome: 'João Silva', cpf: '529.982.247-25' }),
    { pedido: '55002', cpf: '52998224725' })
  assert.equal(L.rotuloNota({ numero: '10234', serie: '1', chave: 'x', emitida_em: null, valor: 1299, papel: 'produto' }), 'NF 10234 · série 1 · produto · R$ 1.299,00')

  // §6: as permissões.
  assert.deepEqual(L.acessoGarantia(ADMIN), { consulta: true, cadastra: true, registra: true, veCpf: true, ve: true, log: true })
  assert.deepEqual(L.acessoGarantia(CONSULTA), { consulta: true, cadastra: false, registra: false, veCpf: false, ve: true, log: false })
  assert.deepEqual(L.acessoGarantia(REGISTRA), { consulta: false, cadastra: false, registra: true, veCpf: false, ve: true, log: false })
  assert.deepEqual(L.acessoGarantia(COMUM), { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false })
  assert.equal(L.acessoGarantia({ role: 'user', permissions: { garantias_cpf: { view: true } } }).veCpf, true)
  assert.equal(L.acessoGarantia(null).ve, false)
  // Só o `view` de "Registrar atendimento": não vê nada (o backend também recusa).
  assert.deepEqual(L.acessoGarantia({ role: 'user', permissions: { garantias_atendimento: { view: true } } }), { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false })
}

// ═══════════════════════════════════════════════════════════════ contrato com o backend
{
  const schemas = api('schemas/garantia.py')
  const servico = api('services/garantia.py')
  const rotas = api('routers/garantias.py')
  const camposPy = (classe) => {
    const ini = schemas.indexOf(`\nclass ${classe}(`)
    assert.ok(ini >= 0, `classe ${classe}`)
    const fim = schemas.indexOf('\n\n\nclass ', ini + 1)
    const bloco = schemas.slice(ini + 1, fim < 0 ? undefined : fim)
    const base = /^class \w+\((\w+)\):/.exec(bloco)[1]
    const proprios = [...bloco.matchAll(/^ {4}(\w+): /gm)].map((x) => x[1]).filter((n) => n !== 'model_config')
    return base === 'BaseModel' ? proprios : [...camposPy(base), ...proprios]
  }
  const fonteLib = ler('../lib/garantias.ts')
  const camposTs = (tipo) => {
    const m = new RegExp(`export type ${tipo} = (?:(\\w+) & )?\\{`).exec(fonteLib)
    assert.ok(m, `tipo ${tipo}`)
    // O corpo até a chave que fecha; os objetos de dentro saem (só os campos de 1º nível).
    let i = m.index + m[0].length
    let prof = 1
    const ini = i
    while (prof && i < fonteLib.length) {
      if (fonteLib[i] === '{') prof++
      else if (fonteLib[i] === '}') prof--
      i++
    }
    let corpo = fonteLib.slice(ini, i - 1).replace(/\/\/[^\n]*/g, '')
    while (/\{[^{}]*\}/.test(corpo)) corpo = corpo.replace(/\{[^{}]*\}/g, '')
    const proprios = [...corpo.matchAll(/(?:^|[\s;])(\w+)\??:/g)].map((x) => x[1])
    return m[1] ? [...camposTs(m[1]), ...proprios] : proprios
  }
  for (const [py, tsTipo] of [
    ['GarantiaLinhaOut', 'GarantiaLinha'], ['IndicadoresOut', 'Indicadores'], ['GarantiaListaOut', 'GarantiaLista'],
    ['PrazoOut', 'Prazo'], ['EntregaOut', 'Entrega'], ['ItemOut', 'ItemPedido'], ['AnexoOut', 'AnexoGarantia'],
    ['AtendimentoOut', 'AtendimentoGarantia'], ['PermissoesOut', 'PermissoesGarantia'], ['GarantiaSalvaOut', 'GarantiaDetalhe'],
    ['LogOut', 'LogLinha'], ['NotaSugeridaOut', 'NotaDoPedido'], ['PedidoParaCadastroOut', 'PedidoParaCadastro'],
    ['VinculoOut', 'Vinculo'], ['SituacaoConversaOut', 'SituacaoConversa'], ['RegrasOut', 'Regras'],
    ['ListaFiltro', 'CorpoLista'],
  ]) {
    assert.deepEqual(camposTs(tsTipo).sort(), camposPy(py).sort(), `${tsTipo} = ${py}`)
  }
  // O que a tela MANDA = o que o backend aceita (extra="forbid").
  assert.deepEqual(camposPy('GarantiaCreate').sort(), ['cliente_nome', 'cpf', 'nf_numero', 'nf_serie', 'pedido'])
  assert.deepEqual(camposPy('AtendimentoCreate').sort(), ['conversa_id', 'mensagem_ids', 'resumo', 'solucao', 'tipo_problema'])
  assert.deepEqual(camposPy('BuscaVinculo'), ['q'])
  // Os pontos do §8 que a tela mostra quando o GET /regras falha = as constantes do servidor.
  const constante = (n) => (new RegExp(`^${n} = (.+?)(?:\\s+#.*)?$`, 'm').exec(servico) || [])[1]
  assert.equal(String(L.REGRAS_PADRAO.ultimo_dia_coberto), { True: 'true', False: 'false' }[constante('ULTIMO_DIA_COBERTO')])
  assert.equal(String(L.REGRAS_PADRAO.cadastro_antes_da_entrega), { True: 'true', False: 'false' }[constante('CADASTRO_ANTES_DA_ENTREGA')])
  assert.equal(String(L.REGRAS_PADRAO.vinculo_automatico), { True: 'true', False: 'false' }[constante('VINCULO_AUTOMATICO')])
  assert.equal(String(L.REGRAS_PADRAO.recalcular_quando_entrega_mudar), { True: 'true', False: 'false' }[constante('RECALCULAR_QUANDO_ENTREGA_MUDAR')])
  assert.equal(`"${L.REGRAS_PADRAO.garantia_por}"`, constante('GARANTIA_POR'))
  assert.equal(String(L.REGRAS_PADRAO.meses_hardware), constante('MESES_HARDWARE'))
  assert.equal(String(L.REGRAS_PADRAO.meses_software), constante('MESES_SOFTWARE'))
  assert.equal(String(L.REGRAS_PADRAO.dias_alerta_hardware), constante('DIAS_ALERTA_HARDWARE'))
  assert.equal(String(L.REGRAS_PADRAO.dias_pausa_atendimento), constante('DIAS_PAUSA_NOVO_ATENDIMENTO'))
  assert.equal(`"${L.ENTREGUE_SEM_DATA.rotulo}"`, constante('ROTULO_ENTREGUE_SEM_DATA'))
  // Os rótulos de status são os do servidor.
  for (const [cod, info] of Object.entries(L.STATUS)) assert.match(servico, new RegExp(`: "${info.rotulo}",`), `rótulo ${cod}`)
  // Todo código de erro do router tem frase; todo aviso e motivo de anexo também.
  const codigos = [...new Set([...rotas.matchAll(/_erro\(\d+, "([a-z_]+)"/g)].map((m) => m[1]))]
  assert.ok(codigos.length >= 12)
  for (const c of codigos) assert.ok(L.ERROS[c], `frase para ${c}`)
  for (const c of [...servico.matchAll(/avisos\.append\("([a-z_]+)"\)/g), ...rotas.matchAll(/avisos\.append\("([a-z_]+)"\)/g)].map((m) => m[1])) assert.ok(L.AVISOS[c], `aviso ${c}`)
  for (const c of ['aguardando_entrega', 'entregue_sem_data']) assert.ok(servico.includes(`"${c}"`) && L.AVISOS[c], `aviso ${c}`)
  for (const c of ['sem_link', 'link_expirado', 'maior_que_8mb', 'tipo_nao_suportado', 'host_nao_permitido', 'limite_de_anexos', 'erro_ao_baixar']) {
    assert.ok(servico.includes(`"${c}"`), `motivo ${c} existe no backend`)
    assert.ok(L.MOTIVOS_ANEXO[c], `motivo ${c} tem frase`)
  }
  const acoes = /GARANTIA_LOG_ACOES = \(([\s\S]*?)\n\)/.exec(api('models/garantia.py'))[1]
  const acoesPy = [...acoes.matchAll(/^ {4}"([a-z_]+)",/gm)].map((m) => m[1])
  assert.deepEqual(acoesPy, ['cadastrou', 'alterou', 'consultou', 'registrou_atendimento', 'recalculou', 'buscou_cpf'])
  for (const a of acoesPy) assert.ok(L.ACOES_LOG[a], `ação ${a}`)
  // As rotas que a tela chama existem (lista e busca são POST: o termo vai no corpo).
  assert.ok(!rotas.includes('@router.get("",') && !rotas.includes('@router.get("/busca"'), 'nada de busca por GET')
  for (const r of ['@router.post("/lista"', '@router.get("/regras"', '@router.get("/pedido"', '@router.post("/busca"', '@router.get("/conversa/{conversa_id}"',
    '@router.post("",', '@router.get("/{garantia_id}"', '@router.put("/{garantia_id}"', '@router.post("/{garantia_id}/recalcular"',
    '"/{garantia_id}/atendimentos"', '@router.get("/{garantia_id}/log"', '@router.get("/anexos/{anexo_id}")']) {
    assert.ok(rotas.includes(r), `rota ${r}`)
  }
}

// ═══════════════════════════════════════════════════════════════ permissões e menu
{
  const can = loadLib('../composables/useCan.ts', { useAuthStore: () => ({ user: null }) })
  const posVenda = can.RESOURCE_GROUPS.find((g) => g.label === 'Pós-venda')
  for (const r of ['garantias', 'garantias_atendimento', 'garantias_cpf']) {
    assert.ok(posVenda.resources.includes(r), `${r} na tela de Permissões (Pós-venda)`)
    assert.ok(can.RESOURCE_LABELS[r], `${r} tem rótulo`)
    assert.match(api('schemas/permissions.py'), new RegExp(`^ {4}"${r}",$`, 'm'), `${r} no backend`)
  }
  assert.match(can.RESOURCE_LABELS.garantias, /Consultar[\s\S]*Cadastrar/)
  assert.match(can.RESOURCE_LABELS.garantias_atendimento, /Registrar atendimento/)
  assert.match(can.RESOURCE_LABELS.garantias_cpf, /CPF/)

  const nav = loadLib('../lib/navGroups.ts')
  const menuPara = (user) => {
    const { descriptor } = parse(ler('../components/AppSidebar.vue'))
    let src = descriptor.scriptSetup.content
    const nomesIcones = ((src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/) || [])[1]).split(',').map((s) => s.trim()).filter(Boolean)
    src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
    const params = {
      computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick, onMounted: () => {}, onScopeDispose: () => {},
      allowedTabs: nav.allowedTabs, TABS_CADASTROS: nav.TABS_CADASTROS, TABS_NF: nav.TABS_NF, TABS_SISTEMA: nav.TABS_SISTEMA,
      defineProps: () => ({}), withDefaults: (p, d) => ({ ...d, ...p, collapsed: false }), defineEmits: () => () => {},
      useAuthStore: () => ({ user, isAdmin: user.role === 'admin' }), useRoute: () => ({ path: '/' }),
      useRuntimeConfig: () => ({ public: { enableMarketing: false } }), useApi: () => ({ api: async () => ({ count: 0 }) }),
    }
    for (const n of nomesIcones) params[n] = {}
    const nomes = Object.keys(params)
    return new Function(...nomes, transpile(src, ts.ModuleKind.ESNext) + '\nreturn visibleSections')(...nomes.map((n) => params[n])).value
  }
  const rotasDe = (u) => menuPara({ status: 'active', ...u }).flatMap((s) => s.items.map((i) => i.to))
  for (const u of [ADMIN, CONSULTA, CADASTRA]) assert.ok(rotasDe(u).includes('/garantias'), `${u.id} vê Garantias no menu`)
  for (const u of [REGISTRA, COMUM]) assert.ok(!rotasDe(u).includes('/garantias'), `${u.id} não vê (sem Consultar)`)
  const posVendaMenu = menuPara({ status: 'active', ...CONSULTA, atendimento: true }).find((s) => s.label === 'Pós-venda')
  assert.deepEqual(posVendaMenu.items.map((i) => i.to), ['/devolucoes', '/reembolso', '/logistica', '/notas-fiscais', '/chamados', '/garantias', '/atendimento'])
  assert.equal(posVendaMenu.items.find((i) => i.to === '/garantias').label, 'Garantias')

  // O composable lê do /me (a mesma regra da lib).
  const comp = loadLib('../composables/useGarantiaAcesso.ts', { useAuthStore: () => ({ user: REGISTRA }) }, requerer)
  assert.deepEqual(comp.useGarantiaAcesso().value, L.acessoGarantia(REGISTRA))
}

// ═══════════════════════════════════════════════════════════════ página /garantias
async function testarPagina() {
  const LISTA = {
    itens: [LINHA, { ...LINHA, id: 8, cliente_nome: 'Maria Souza', cpf_mascarado: '***.111.222-**', nf_numero: '10198', nf_serie: '', data_inicio: '2026-06-02', fim_hardware: '2026-09-02', fim_software: '2027-06-02', status: 'somente_software', status_rotulo: 'Somente software', atendimentos: 0 }],
    total: 2,
    indicadores: { ativas: 128, somente_software: 342, hw_vence_30d: 17, atendimentos_no_mes: 23, aguardando_entrega: 4, entregue_sem_data: 3, expiradas: 9, total: 503 },
    hoje: '2026-10-07',
  }
  const chamadas = []
  const fakeApi = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/garantias/lista' && opts.method === 'POST') return LISTA
    if (url === '/api/garantias/regras') return { ...L.REGRAS_PADRAO, status: {}, coberturas: {}, origens_entrega: {} }
    throw new Error(`chamada inesperada ${url}`)
  }
  // O middleware de permissão: Consultar.
  {
    const g = { user: CONSULTA, api: fakeApi }
    componente('../pages/garantias.vue', g)
    const pg = sfc('../pages/garantias.vue')
    let meta
    new Function('definePageMeta', (pg.descriptor.scriptSetup.content.match(/definePageMeta\([^\n]*\)\n/) || [''])[0])((m) => { meta = m })
    assert.deepEqual(meta, { middleware: ['permission'], permission: { resource: 'garantias', action: 'view' } })
  }
  // Carrega a lista e os indicadores; os cartões filtram.
  const replaces = []
  const m = montar('../pages/garantias.vue', {}, { user: CONSULTA, api: fakeApi, route: { query: {} }, router: { replace: async (x) => replaces.push(x) } })
  await m.montar()
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(m.estado.itens.length, 2)
  assert.equal(m.estado.indicadores.ativas, 128)
  assert.deepEqual(chamadas.find((c) => c.url === '/api/garantias/lista').opts, { method: 'POST', body: { limite: 100, offset: 0 } })
  // A busca (nome/CPF) vai no corpo, nunca na URL.
  m.estado.filtros.busca = '529.982.247-25'
  await m.estado.carregar()
  const comBusca = chamadas.filter((c) => c.url === '/api/garantias/lista').at(-1)
  assert.equal(comBusca.opts.body.busca, '529.982.247-25')
  assert.ok(chamadas.every((c) => !c.url.includes('?') && !c.opts.query), 'nada na query string')
  m.estado.filtros.busca = ''
  m.estado.clicarCartao('hw_vence_30d')
  assert.equal(m.estado.filtros.status, 'hw_vence_30d')
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(chamadas.filter((c) => c.url === '/api/garantias/lista').at(-1).opts.body.status, 'hw_vence_30d', 'o cartão relê a lista filtrada')
  m.estado.limparFiltros()
  assert.equal(m.estado.filtros.status, 'todos')
  // Abrir a linha põe a garantia na URL (?garantia=7).
  m.estado.abrir(7)
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(replaces.at(-1).query.garantia, '7')

  // ?novo=<pedido>: só quem cadastra abre o formulário.
  for (const [u, abre] of [[CADASTRA, true], [CONSULTA, false]]) {
    const x = montar('../pages/garantias.vue', {}, { user: u, api: fakeApi, route: { query: { novo: '55001' } }, router: { replace: async () => {} } })
    await x.montar()
    assert.equal(x.estado.formAberto, abre, `${u.id}: ?novo abre o cadastro = ${abre}`)
    if (abre) assert.equal(x.estado.pedidoInicial, '55001')
  }

  // A tela renderizada (SSR não roda o onMounted: carrega à mão e renderiza de novo com o estado):
  // cabeçalho, + Nova garantia só para quem cadastra, os 4 cartões e a tabela do §4.1.
  const ST = { GarantiaStatus: '../components/GarantiaStatus.vue' }
  {
    const g = { user: CADASTRA, api: fakeApi, route: { query: {} }, router: { replace: async () => {} } }
    const cx = comEstado(componente('../pages/garantias.vue', g))
    await html(appCom(cx.C, {}, g, ST))
    await cx.estado.carregar()
    const h = await html(appCom(cx.mesmo, {}, g, ST))
    assert.match(h, /Painel de Garantia — Uranyx/)
    assert.match(h, /data-nova-garantia/)
    assert.match(h, /placeholder="Nome \/ CPF \/ NF \/ Pedido"/)
    const ths = [...h.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((x) => texto(x[1]))
    assert.deepEqual(ths, ['Nome', 'CPF', 'NF', 'Entrega', 'Fim HW', 'Fim SW', 'Status', 'Atend.'])
    assert.match(h, /\*\*\*\.982\.247-\*\*/, 'CPF mascarado na lista')
    assert.ok(!/529\.982\.247-25|52998224725/.test(h), 'nunca o CPF completo na lista')
    assert.match(texto(h), /07\/10\/26 07\/01\/27 07\/10\/27/, 'Entrega, Fim HW e Fim SW em dd/mm/aa')
    assert.match(h, /🟢/)
    assert.match(h, /🔵/)
    const cartoes = [...h.matchAll(/data-cartao="([a-z_0-9]+)"[\s\S]*?<\/button>/g)].map((x) => [x[1], texto(x[0].replace(/^[^>]*>/, ''))])
    assert.deepEqual(cartoes, [['ativas', 'Ativas 128'], ['somente_software', 'Só software 342'], ['hw_vence_30d', 'HW vence em 30d 17'], ['atendimentos_no_mes', 'Atendimentos no mês 23']])
    assert.match(h, /data-cartoes-garantias/, 'cartões no celular')
    assert.match(texto(h), /4 sem data de entrega \( 3 já entregues no Bling \)/, 'os já entregues sem data aparecem à parte')
  }
  // Sem Cadastrar: sem o + Nova garantia. Carregando e vazio têm estado próprio.
  {
    const g = { user: CONSULTA, api: async (url) => (url === '/api/garantias/lista' ? { ...LISTA, itens: [], total: 0 } : L.REGRAS_PADRAO) }
    const cx = comEstado(componente('../pages/garantias.vue', g))
    const carregandoHtml = await html(appCom(cx.C, {}, g))
    assert.match(carregandoHtml, /data-carregando/)
    assert.ok(!/data-nova-garantia/.test(carregandoHtml))
    await cx.estado.carregar()
    const vazioHtml = await html(appCom(cx.mesmo, {}, g))
    assert.match(vazioHtml, /Nenhuma garantia cadastrada ainda/)
  }
  // Erro de carga: a frase e o "tentar de novo".
  {
    const m2 = montar('../pages/garantias.vue', {}, { user: CONSULTA, api: async () => { throw Object.assign(new Error('x'), { statusCode: 500 }) } })
    await m2.estado.carregar()
    assert.match(m2.estado.erro, /erro 500/)
  }
}

// ═══════════════════════════════════════════════════════════════ cadastro (§3)
async function testarFormulario() {
  const PEDIDO = {
    pedido_bling: '55001', pedido_marketplace: '2000012345', plataforma: 'ml', conta: 'Loja X', data_pedido: '2026-10-01T10:00:00Z', situacao: 'Entregue',
    itens: [{ descricao: 'Celular Uranyx', sku: 'dg5-azul', quantidade: 1, uranyx: true }], produto_uranyx: true,
    entrega: { data: '2026-11-30', origem: 'ml', origem_rotulo: 'Mercado Livre — data oficial da entrega', em: '2026-11-30T15:00:00Z' },
    prazos: { data_inicio: '2026-11-30', fim_hardware: '2027-02-28', fim_software: '2027-11-30', status: 'ativa', status_rotulo: 'Ativa' },
    notas: [{ numero: '10234', serie: '1', chave: 'k1', emitida_em: null, valor: 1299, papel: 'produto' }, { numero: '10235', serie: '1', chave: 'k2', emitida_em: null, valor: 9.9, papel: 'embalagem' }],
    nf_sugerida: { numero: '10234', serie: '1', chave: 'k1', emitida_em: null, valor: 1299, papel: 'produto' },
    nome_sugerido: 'João Silva', nome_origem: 'nf', cpf_sugerido: null, cpf_sugerido_mascarado: '***.982.247-**',
    garantias_existentes: [], avisos: [],
  }
  const chamadas = []
  let respostaPost = null
  const fakeApi = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/garantias/pedido') {
      if (opts.query.numero === '999') throw Object.assign(new Error('404'), { statusCode: 404, data: { detail: { code: 'pedido_nao_encontrado', campo: 'pedido' } } })
      return PEDIDO
    }
    if (url === '/api/garantias' && opts.method === 'POST') {
      if (respostaPost instanceof Error) throw respostaPost
      return respostaPost
    }
    if (opts.method === 'PUT') return { ...respostaPost, id: 7 }
    throw new Error(`inesperada ${url}`)
  }
  const m = montar('../components/GarantiaForm.vue', { garantia: null, pedidoInicial: '55001', regras: L.REGRAS_PADRAO }, { user: CADASTRA, api: fakeApi })
  await m.montar()
  // Ao informar o pedido: entrega → data inicial; fins calculados; sugestões nos campos vazios.
  assert.equal(chamadas[0].url, '/api/garantias/pedido')
  assert.deepEqual(chamadas[0].opts.query, { numero: '55001' })
  assert.equal(m.estado.prazos.data_inicio, '2026-11-30')
  assert.equal(m.estado.prazos.fim_hardware, '2027-02-28', 'RN04: 30/11 → 28/02')
  assert.equal(m.estado.form.nf_numero, '10234')
  assert.equal(m.estado.form.nf_serie, '1')
  assert.equal(m.estado.form.cliente_nome, 'João Silva')
  assert.equal(m.estado.form.cpf, '', 'sem a permissão do CPF o pedido não preenche o CPF')
  assert.equal(m.estado.cpfDoPedidoMascarado, '***.982.247-**')
  // Máscara do CPF enquanto digita.
  m.estado.form.cpf = '52998224724'
  await Vue.nextTick()
  assert.equal(m.estado.form.cpf, '529.982.247-24')
  // CPF inválido bloqueia (nem chama a API).
  const antes = chamadas.length
  await m.estado.salvar()
  assert.match(m.estado.errosCampo.cpf, /inválido/)
  assert.equal(chamadas.length, antes)
  // CPF válido: POST só com o informado.
  m.estado.form.cpf = '529.982.247-25'
  await Vue.nextTick()
  respostaPost = { ...LINHA, id: 11, avisos: [] }
  await m.estado.salvar()
  const post = chamadas.at(-1)
  assert.equal(post.opts.method, 'POST')
  assert.deepEqual(post.opts.body, { pedido: '55001', nf_numero: '10234', nf_serie: '1', cliente_nome: 'João Silva', cpf: '52998224725' })
  assert.deepEqual(m.emitidos.at(-1), ['salva', respostaPost])
  // 409: a garantia que já existe para esse CPF nessa NF.
  respostaPost = Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'garantia_duplicada', campo: 'nf_numero', garantia_id: 3 } } })
  await m.estado.salvar()
  assert.match(m.estado.errosCampo.nf_numero, /Já existe garantia/)
  assert.equal(m.estado.duplicadaId, 3)
  // 422 do servidor no CPF aparece embaixo do campo.
  respostaPost = Object.assign(new Error('422'), { statusCode: 422, data: { detail: { code: 'cpf_invalido', campo: 'cpf' } } })
  await m.estado.salvar()
  assert.match(m.estado.errosCampo.cpf, /CPF inválido/)
  // Pedido que não existe: erro embaixo do pedido e sem prévia.
  m.estado.form.pedido = '999'
  await Vue.nextTick()
  assert.equal(m.estado.consulta, null, 'trocar o nº apaga a prévia')
  await m.estado.buscarPedido()
  assert.match(m.estado.errosCampo.pedido, /Pedido não encontrado/)

  // O template: a data inicial é texto travado, não campo; nenhum input de data/prazo.
  const tpl = sfc('../components/GarantiaForm.vue').descriptor.template.content
  assert.match(tpl, /data-data-inicial/)
  assert.ok(!/v-model="form\.(data_inicio|fim_hardware|fim_software|status|criado_em)"/.test(tpl))
  assert.ok(!/type="date"/.test(tpl), 'sem campo de data no cadastro')
  for (const c of ['nf_numero', 'cliente_nome', 'cpf', 'pedido']) assert.match(tpl, new RegExp(`data-erro-campo="${c}"`), `erro embaixo de ${c}`)

  // Renderizado: Nova garantia com os prazos travados.
  const html = await renderizar('../components/GarantiaForm.vue', { garantia: null, pedidoInicial: null, regras: L.REGRAS_PADRAO }, { user: CADASTRA, api: fakeApi }, { GarantiaStatus: '../components/GarantiaStatus.vue' })
  assert.match(html, /Nova garantia/)
  assert.match(html, /data-data-inicial[^>]*>\s*informe o pedido/, 'sem pedido, a data inicial espera o pedido')
  assert.match(html, /hardware \+3 meses · software \+12 meses · vale até o último dia, inclusive/)

  // Correção: manda só o que mudou; CPF mascarado fica de fora.
  const DET = { ...LINHA, cpf: '***.982.247-**', cpf_completo: false, nf_chave: null, nf_emitente_cnpj: null, itens: [], entrega: PEDIDO.entrega, hardware: null, software: null, criado_por: null, atualizado_em: null, atualizado_por: null, atendimentos_lista: [], permissoes: { cadastrar: true, registrar_atendimento: false, ver_cpf: false, ver_log: false } }
  const chamadasE = []
  const e = montar('../components/GarantiaForm.vue', { garantia: DET, pedidoInicial: null, regras: L.REGRAS_PADRAO }, {
    user: CADASTRA, api: async (url, opts = {}) => { chamadasE.push({ url, opts }); return { ...DET, cliente_nome: 'João da Silva' } },
  })
  await e.montar()
  assert.equal(chamadasE.length, 0, 'na correção não busca o pedido ao abrir')
  assert.equal(e.estado.form.cpf, '', 'sem a permissão, o CPF não vem para o campo')
  e.estado.form.cliente_nome = 'João da Silva'
  await e.estado.salvar()
  assert.deepEqual(chamadasE.at(-1), { url: '/api/garantias/7', opts: { method: 'PUT', body: { cliente_nome: 'João da Silva' } } })
}

// ═══════════════════════════════════════════════════════════════ detalhe (§4.2)
async function testarDetalhe() {
  const AT = {
    id: 1, garantia_id: 7, data_atendimento: '2026-12-01T13:00:00Z', atendente: { id: 'u', nome: 'Ana' },
    conversa_id: 'c1', conversa_link: '/atendimento?conversa=c1', conversa_plataforma: 'shopee', conversa_canal: 'chat', conversa_conta: 'Loja S', conversa_pedido: '251001ABC',
    resumo: 'tela piscando', mensagens: [{ id: 'm1', autor: 'cliente', origem: null, tipo: 'texto', texto: 'a tela pisca', enviada_em: '2026-12-01T13:00:00Z', anexos: [] }],
    anexos: [
      { mensagem_id: 'm1', tipo: 'imagem', nome: 'foto.jpg', url_original: 'https://cf.shopee.com.br/x', baixado: true, motivo: null, arquivo_url: '/api/garantias/anexos/5', content_type: 'image/jpeg', tamanho: 2048 },
      { mensagem_id: 'm1', tipo: 'arquivo', nome: 'video.mp4', url_original: 'https://cf.shopee.com.br/y', baixado: false, motivo: 'maior_que_8mb', arquivo_url: null, content_type: null, tamanho: null },
    ],
    tipo_problema: 'hardware', cobertura: 'coberto', cobertura_rotulo: 'Coberto', fim_considerado: '2027-01-07',
    cobertura_hoje: 'fora_da_garantia', cobertura_hoje_rotulo: 'Fora da garantia', solucao: 'troca pela assistência', criado_em: '2026-12-01T13:05:00Z',
  }
  const DET = {
    ...LINHA, cpf: '529.982.247-25', cpf_completo: true, nf_chave: '3526…', nf_emitente_cnpj: null,
    itens: [{ descricao: 'Celular Uranyx', sku: 'dg5', quantidade: 1, uranyx: true }],
    entrega: { data: '2026-10-07', origem: 'ml', origem_rotulo: 'Mercado Livre — data oficial da entrega', em: null, verificada_em: '2026-10-07T16:00:00Z' },
    hardware: { fim: '2027-01-07', dias_total: 92, dias_restantes: 92, coberto_hoje: true },
    software: { fim: '2027-10-07', dias_total: 365, dias_restantes: 365, coberto_hoje: true },
    criado_por: { id: 'u', nome: 'Ana' }, atualizado_em: null, atualizado_por: null,
    atendimentos_lista: [AT], permissoes: { cadastrar: true, registrar_atendimento: true, ver_cpf: true, ver_log: true },
  }
  const chamadas = []
  const fakeApi = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/garantias/7') return DET
    if (url === '/api/garantias/7/log') return [{ id: 1, acao: 'consultou', campo: null, valor_anterior: null, valor_novo: null, detalhe: 'viu o CPF completo', pessoa: { id: 'u', nome: 'Ana' }, em: '2026-10-07T16:00:00Z' }]
    // A resposta do "conferir" vem com o CPF mascarado (só o GET revela).
    if (url === '/api/garantias/7/recalcular') return { ...DET, data_inicio: '2026-10-08', cpf: '***.982.247-**', cpf_completo: false }
    throw new Error(url)
  }
  const m = montar('../components/GarantiaDetalhe.vue', { garantiaId: 7, regras: L.REGRAS_PADRAO, avisos: ['cpf_diferente_do_pedido'], versao: 0 }, { user: ADMIN, api: fakeApi })
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(chamadas[0].url, '/api/garantias/7', 'abrir = GET (o servidor grava quem consultou)')
  assert.deepEqual(m.estado.abas.map((a) => a.v), ['dados', 'atendimentos', 'log'])
  m.estado.aba = 'log'
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(chamadas.at(-1).url, '/api/garantias/7/log')
  assert.equal(m.estado.log.length, 1)
  await m.estado.conferirEntrega()
  assert.deepEqual(chamadas.at(-1), { url: '/api/garantias/7/recalcular', opts: { method: 'POST' } })
  assert.deepEqual(m.emitidos.at(-1), ['mudou'])
  assert.deepEqual([m.estado.g.data_inicio, m.estado.g.cpf, m.estado.g.cpf_completo], ['2026-10-08', '529.982.247-25', true], 'o CPF que o GET já mostrou fica')

  // Renderizado: dados, barras, aba Atendimentos com Coberto/Fora e os anexos.
  const EX = { GarantiaStatus: '../components/GarantiaStatus.vue', GarantiaPrazoBarra: '../components/GarantiaPrazoBarra.vue', GarantiaCobertura: '../components/GarantiaCobertura.vue' }
  const abrirDetalhe = async (user, det) => {
    const g = { user, api: async () => det }
    const cx = comEstado(componente('../components/GarantiaDetalhe.vue', g))
    const props = { garantiaId: 7, regras: L.REGRAS_PADRAO, avisos: [] }
    await html(appCom(cx.C, props, g, EX))
    await new Promise((r) => setTimeout(r, 0))
    return { cx, aba: async (a) => { cx.estado.aba = a; return html(appCom(cx.mesmo, props, g, EX)) } }
  }
  const d1 = await abrirDetalhe(ADMIN, DET)
  const dados = await d1.aba('dados')
  assert.match(dados, /529\.982\.247-25/, 'CPF completo no detalhe para quem pode')
  assert.equal((dados.match(/role="progressbar"/g) || []).length, 2, 'barra de HW e de SW')
  assert.match(texto(dados), /Hardware \(3 meses\) até 07\/01\/2027/)
  assert.match(texto(dados), /92 dias restantes/)
  assert.match(texto(dados), /Cadastrado por Ana/)
  assert.match(dados, /Conferir entrega agora/)
  assert.match(dados, /Corrigir dados/)
  const atend = await d1.aba('atendimentos')
  assert.match(atend, /data-linha-do-tempo/)
  assert.match(texto(atend), /✅ Coberto/)
  assert.match(texto(atend), /Com os prazos de hoje \(entrega corrigida depois\): Fora da garantia/)
  assert.match(atend, /href="\/atendimento\?conversa=c1"/)
  assert.match(atend, /<img src="\/api\/garantias\/anexos\/5"/)
  assert.match(texto(atend), /maior que 8 MB — ficou só o link/)
  assert.ok(!/<button[^>]*>[^<]*(Editar|Apagar)/i.test(atend), 'nada de editar/apagar atendimento')

  // Sem permissão de CPF/log/cadastro: CPF mascarado com o selo, sem aba Log nem botões.
  const DET2 = { ...DET, cpf: '***.982.247-**', cpf_completo: false, permissoes: { cadastrar: false, registrar_atendimento: false, ver_cpf: false, ver_log: false } }
  const d2 = await abrirDetalhe(CONSULTA, DET2)
  assert.deepEqual(d2.cx.estado.abas.map((a) => a.v), ['dados', 'atendimentos'])
  const h2 = await d2.aba('dados')
  assert.match(texto(h2), /\*\*\*\.982\.247-\*\* mascarado/)
  assert.ok(!/Corrigir dados|Conferir entrega/.test(h2))

  // Pedido entregue no Bling sem data no DaVinci: o selo e o texto dizem isso (não "aguardando").
  const DET3 = { ...DET, data_inicio: null, fim_hardware: null, fim_software: null, status: 'aguardando_entrega', status_rotulo: 'Entregue — sem data no DaVinci', entregue_sem_data: true, hardware: null, software: null, entrega: { data: null, origem: null, origem_rotulo: null, em: null, verificada_em: '2026-10-07T16:00:00Z' } }
  const d3 = await abrirDetalhe(CONSULTA, DET3)
  const h3 = await d3.aba('dados')
  assert.match(texto(h3), /🟡 Entregue — sem data no DaVinci/)
  assert.match(h3, /data-entregue-sem-data-texto/)
  assert.match(texto(h3), /início sem data de entrega/)
  assert.ok(!/O pedido ainda não tem data de entrega/.test(h3))
}

// ═══════════════════════════════════════════════════════════════ /atendimento: Vincular à garantia (§5)
async function testarVincular() {
  const CONVERSA = { id: 'c1', plataforma: 'shopee', conta: 'Loja S', pedido_marketplace: '251001ABC', ultima_mensagem_em: '2026-12-02T10:00:00Z', canal: 'chat' }
  const MSGS = [
    { id: 'm1', autor: 'cliente', origem: 'externo', autor_nome: null, tipo: 'texto', texto: 'a tela pisca', anexos: [{ tipo: 'imagem', url: 'https://cf.shopee.com.br/a.jpg' }], enviada_em: '2026-12-01T13:00:00Z', status: 'ok', erro: null },
    { id: 'm2', autor: 'loja', origem: 'externo', autor_nome: null, tipo: 'texto', texto: 'pode reiniciar?', anexos: null, enviada_em: '2026-12-01T14:00:00Z', status: 'ok', erro: null },
    { id: 'md', autor: 'mediador', origem: 'sistema', autor_nome: null, tipo: 'texto', texto: 'mediação aberta', anexos: null, enviada_em: '2026-12-01T15:00:00Z', status: 'ok', erro: null },
    { id: 'm3', autor: 'cliente', origem: 'externo', autor_nome: null, tipo: 'texto', texto: 'continua', anexos: null, enviada_em: '2027-01-08T12:00:00Z', status: 'ok', erro: null },
    { id: 's1', autor: 'sistema', origem: 'sistema', autor_nome: null, tipo: 'texto', texto: 'pedido entregue', anexos: null, enviada_em: '2026-12-01T09:00:00Z', status: 'ok', erro: null },
  ]
  const SITUACAO = {
    conversa_id: 'c1', pedido_encontrado: true, pedido_bling: '55001', pedido_marketplace: '251001ABC', produto_uranyx: true,
    cpf_conhecido: true, alerta_cpf_sem_garantia: false, garantias: [LINHA], busca_sugerida: '55001', vinculos: [],
  }
  const chamadas = []
  let resposta = null
  const fakeApi = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/garantias/regras') return L.REGRAS_PADRAO
    if (url === '/api/garantias/busca' && opts.method === 'POST') return [LINHA, { ...LINHA, id: 8 }]
    if (url === '/api/garantias/7/atendimentos') {
      if (resposta instanceof Error) throw resposta
      return resposta
    }
    throw new Error(url)
  }
  const m = montar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: SITUACAO }, { user: REGISTRA, api: fakeApi })
  await m.montar()
  // Busca já preenchida com o pedido da conversa (ponto 4) e a garantia do CPF/pedido já escolhida.
  assert.equal(m.estado.busca, '55001')
  assert.equal(m.estado.escolhida.id, 7)
  assert.ok(!chamadas.some((c) => c.url === '/api/garantias/busca'), 'com as garantias da conversa, não precisa buscar')
  // As mensagens para escolher: sem as de sistema, a mais nova em cima; a do mediador (ML) entra,
  // como na cópia padrão do servidor.
  assert.deepEqual(m.estado.mensagensEscolhiveis.map((x) => x.id), ['m3', 'md', 'm2', 'm1'])
  // Prévia da cobertura antes de salvar: sem escolha, a 1ª do cliente no trecho atual — o
  // cliente voltou 38 dias depois, trecho novo: 08/01/2027.
  assert.equal(L.diaEmSP(m.estado.quando), '2027-01-08')
  m.estado.tipo = 'hardware'
  assert.equal(m.estado.previa.cobertura, 'fora_da_garantia', 'HW terminou em 07/01/2027')
  m.estado.tipo = 'software'
  assert.equal(m.estado.previa.cobertura, 'coberto')
  m.estado.tipo = 'hardware'
  m.estado.alternar('m1')
  assert.equal(m.estado.previa.cobertura, 'coberto', 'escolhendo a 1ª mensagem (01/12) fica Coberto')
  assert.deepEqual(m.estado.anexos.map((a) => a.url), ['https://cf.shopee.com.br/a.jpg'], 'os anexos das mensagens escolhidas')
  // Validação: solução obrigatória.
  await m.estado.vincular()
  assert.match(m.estado.erros.solucao, /Informe a solução/)
  assert.ok(!chamadas.some((c) => c.url.endsWith('/atendimentos')))
  // Salvar: o corpo do POST.
  m.estado.solucao = 'troca pela assistência'
  resposta = { id: 1, garantia_id: 7, cobertura: 'coberto', cobertura_rotulo: 'Coberto', tipo_problema: 'hardware' }
  await m.estado.vincular()
  const post = chamadas.at(-1)
  assert.equal(post.url, '/api/garantias/7/atendimentos')
  assert.deepEqual(post.opts, { method: 'POST', body: { conversa_id: 'c1', tipo_problema: 'hardware', solucao: 'troca pela assistência', mensagem_ids: ['m1'] } })
  assert.deepEqual(m.emitidos.at(-1), ['vinculado', resposta])
  // Sem mensagens escolhidas e com resumo: sem mensagem_ids, com o resumo.
  m.estado.alternar('m1')
  m.estado.resumo = 'tela piscando'
  await m.estado.vincular()
  assert.deepEqual(chamadas.at(-1).opts.body, { conversa_id: 'c1', tipo_problema: 'hardware', solucao: 'troca pela assistência', resumo: 'tela piscando' })
  // Clique duplo: o 409 vira frase.
  resposta = Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'atendimento_repetido', atendimento_id: 1 } } })
  await m.estado.vincular()
  assert.match(m.estado.erroGeral, /acabou de ser vinculado/)

  // Sem garantia na conversa: busca pelo que veio preenchido.
  const chamadas2 = []
  const m2 = montar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [], alerta_cpf_sem_garantia: true } }, {
    user: REGISTRA, api: async (url, opts) => { chamadas2.push({ url, opts }); return url === '/api/garantias/busca' ? [] : L.REGRAS_PADRAO },
  })
  await m2.montar()
  // POST com o termo no corpo (pode ser CPF: nada na URL).
  assert.deepEqual(chamadas2.find((c) => c.url === '/api/garantias/busca').opts, { method: 'POST', body: { q: '55001' } })
  assert.equal(m2.estado.escolhida, null)
  await m2.estado.vincular()
  assert.match(m2.estado.erros.garantia, /Escolha a garantia/)
  assert.match(m2.estado.erros.tipo_problema, /Hardware ou Software/)

  // O alerta de CPF sem garantia aparece no modal (e o link de cadastrar só para quem cadastra).
  const html = await renderizar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [], alerta_cpf_sem_garantia: true } }, { user: REGISTRA, api: fakeApi }, { GarantiaStatus: '../components/GarantiaStatus.vue' })
  assert.match(html, /data-alerta-cpf-sem-garantia/)
  assert.match(html, /não tem garantia cadastrada/)
  assert.ok(!/garantias\?novo=/.test(html), 'sem Cadastrar, sem o link')
  const htmlAdm = await renderizar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [], alerta_cpf_sem_garantia: true } }, { user: ADMIN, api: fakeApi }, { GarantiaStatus: '../components/GarantiaStatus.vue' })
  assert.match(htmlAdm, /href="\/garantias\?novo=55001"/)
  assert.match(texto(html), /Data do atendimento: .* \(1ª mensagem do cliente no trecho atual da conversa — pausa de até 7 dias não abre trecho novo\)/)

  // A única garantia do CPF é de OUTRO pedido (outro aparelho): aparece, marcada "outro pedido",
  // e NÃO vem escolhida — atendimento vinculado não se apaga.
  const OUTRA = { ...LINHA, id: 9, pedido_bling: '99999' }
  const m3 = montar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [OUTRA] } }, {
    user: REGISTRA, api: async (url, opts) => (url === '/api/garantias/busca' ? [OUTRA] : L.REGRAS_PADRAO),
  })
  await m3.montar()
  assert.equal(m3.estado.escolhida, null)
  assert.equal(m3.estado.outroPedido(OUTRA), true)
  await m3.estado.buscar()
  assert.equal(m3.estado.escolhida, null, 'nem quando a busca devolve só ela')
  const htmlOutra = await renderizar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [OUTRA] } }, { user: REGISTRA, api: fakeApi }, { GarantiaStatus: '../components/GarantiaStatus.vue' })
  assert.match(htmlOutra, /data-outro-pedido/)
  assert.match(htmlOutra, /data-so-outros-pedidos/)
  assert.ok(!/aria-checked="true"[^>]*>[\s\S]*?#9/.test(htmlOutra))
  // Duas do mesmo pedido (NF de produto e de embalagem): nenhuma vem marcada.
  const m4 = montar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [LINHA, { ...LINHA, id: 8 }] } }, { user: REGISTRA, api: fakeApi })
  assert.equal(m4.estado.escolhida, null)
}

// ═══════════════════════════════════════════════════════════════ bloco do painel Pedido
async function testarBloco() {
  const SIT = { conversa_id: 'c1', pedido_encontrado: true, pedido_bling: '55001', pedido_marketplace: '251001ABC', produto_uranyx: true, cpf_conhecido: true, alerta_cpf_sem_garantia: true, garantias: [], busca_sugerida: '55001', vinculos: [] }
  const extras = { GarantiaStatus: '../components/GarantiaStatus.vue', GarantiaCobertura: '../components/GarantiaCobertura.vue' }
  const com = await renderizar('../components/AtendimentoGarantia.vue', { situacao: SIT, podeVincular: true, podeAbrir: false, podeCadastrar: true }, {}, extras)
  assert.match(com, /data-alerta-cpf-sem-garantia/)
  assert.match(com, /CPF sem garantia cadastrada/)
  assert.match(com, /data-vincular-garantia-botao/)
  assert.match(com, /href="\/garantias\?novo=55001"/)
  const sem = await renderizar('../components/AtendimentoGarantia.vue', { situacao: { ...SIT, alerta_cpf_sem_garantia: false, garantias: [LINHA], vinculos: [{ atendimento_id: 1, garantia_id: 7, data_atendimento: '2026-12-01T13:00:00Z', tipo_problema: 'hardware', cobertura: 'coberto', cobertura_rotulo: 'Coberto' }] }, podeVincular: false, podeAbrir: true, podeCadastrar: false }, {}, extras)
  assert.ok(!/data-vincular-garantia-botao/.test(sem), 'sem Registrar atendimento, sem o botão')
  assert.match(sem, /href="\/garantias\?garantia=7"/)
  assert.match(texto(sem), /Esta conversa já foi vinculada/)
  assert.match(texto(sem), /🟢 Ativa/)
  assert.ok(!/data-outro-pedido/.test(sem), 'a garantia é do pedido da conversa')
  const outro = await renderizar('../components/AtendimentoGarantia.vue', { situacao: { ...SIT, alerta_cpf_sem_garantia: false, garantias: [{ ...LINHA, pedido_bling: '99999', status: 'aguardando_entrega', entregue_sem_data: true }] }, podeVincular: true, podeAbrir: true, podeCadastrar: false }, {}, extras)
  assert.match(outro, /data-outro-pedido/, 'garantia do mesmo CPF em outro pedido: marcada')
  assert.match(texto(outro), /Entregue — sem data no DaVinci/)
  // Nada para mostrar e sem poder vincular: o bloco some.
  const nada = await renderizar('../components/AtendimentoGarantia.vue', { situacao: { ...SIT, alerta_cpf_sem_garantia: false, produto_uranyx: false }, podeVincular: false, podeAbrir: true, podeCadastrar: false }, {}, extras)
  assert.ok(!/data-bloco-garantia/.test(nada))

  // Ligações na conversa e no painel do pedido.
  const conversa = sfc('../components/AtendimentoConversa.vue').descriptor
  const tela = conversa.template.content
  const setup = conversa.scriptSetup.content
  // O botão do cabeçalho fica FORA do `canEdit` (a trava de só leitura não bloqueia).
  const inicioCanEdit = tela.indexOf('<template v-if="!conversa.somente_leitura && canEdit">')
  const fimCanEdit = tela.indexOf('</template>', tela.indexOf('fecharOuReabrir', inicioCanEdit))
  const botao = tela.indexOf('data-vincular-garantia-cabecalho')
  assert.ok(inicioCanEdit > 0 && fimCanEdit > inicioCanEdit && botao > fimCanEdit, 'o botão vem depois do bloco canEdit')
  assert.match(tela, /<Button\s+v-if="podeVincularGarantia"/)
  assert.match(setup, /const podeVincularGarantia = computed\(\(\) => garantiaAcesso\.value\.registra && !!conversa\.value && !conversa\.value\.id\.startsWith\('ig:'\)\)/)
  assert.ok(!/podeVincularGarantia = computed\([^\n]*canEdit/.test(setup), 'não depende do canEdit')
  assert.match(setup, /\/api\/garantias\/conversa\/\$\{encodeURIComponent\(id\)\}/)
  assert.match(tela, /<GarantiaVincular\s+v-if="vincularAberto && detalhe && podeVincularGarantia"/)
  assert.match(tela, /<AtendimentoPedido[\s\S]*?:garantia="garantiaSituacao"[\s\S]*?:garantia-acesso="garantiaAcesso"[\s\S]*?@vincular-garantia="vincularAberto = true"/)
  // A função carregarGarantia: só para quem vê a garantia, nunca no Direct.
  {
    const ini = setup.indexOf('let geracaoGarantia = 0')
    const fim = setup.indexOf('const podeVincularGarantia')
    const js = transpile(setup.slice(ini, fim)) + '\nreturn carregarGarantia'
    const rodar = async (acesso, id, resposta) => {
      const chamadas = []
      const situ = Vue.ref('x')
      const fn = new Function('garantiaAcesso', 'garantiaSituacao', 'garantiaCarregando', 'garantiaErro', 'api', 'props', 'statusDoErro', 'errosDaGarantia', js)(
        Vue.ref(acesso), situ, Vue.ref(false), Vue.ref(null), async (url) => { chamadas.push(url); return resposta }, { conversaId: id }, () => 0, L.errosDaApi,
      )
      await fn(id)
      return { chamadas, situ: situ.value }
    }
    assert.deepEqual(await rodar(L.acessoGarantia(COMUM), 'c1', {}), { chamadas: [], situ: null }, 'sem permissão não busca')
    assert.deepEqual(await rodar(L.acessoGarantia(REGISTRA), 'ig:123', {}), { chamadas: [], situ: null }, 'Direct do Instagram não')
    assert.deepEqual(await rodar(L.acessoGarantia(REGISTRA), 'c1', { ok: 1 }), { chamadas: ['/api/garantias/conversa/c1'], situ: { ok: 1 } })
  }
  const pedido = sfc('../components/AtendimentoPedido.vue').descriptor.template.content
  assert.match(pedido, /<AtendimentoGarantia\s+v-if="garantiaAcesso\?\.ve"[\s\S]*?:pode-vincular="garantiaAcesso\.registra"/)
  assert.ok(!/<AtendimentoGarantia[^>]*canEdit/.test(pedido), 'o bloco não usa o canEdit')
}

;(async () => {
  await testarPagina()
  await testarFormulario()
  await testarDetalhe()
  await testarVincular()
  await testarBloco()
  console.log('ok: garantias-sfc')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
