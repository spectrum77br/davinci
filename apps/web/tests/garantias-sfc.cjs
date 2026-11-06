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
// As 3 partes (04/11/2026): as pílulas Celular/Mala/Eletro e as abas Resumo (só admin) · Garantia ·
// Atendimento (?aba=, ?dept=), o Resumo com Saldo e a % = gasto ÷ saldo × 100, a coluna Loja, o selo
// do Atendimento e o cartão do pedido que a busca achou sem garantia.
// Garantia × SAC (06/11/2026, "Painel de Garantia: alterações" v3): o prazo único por produto (sem
// hardware/software no painel), as 10 colunas da Tabela, a coluna Atendimento com o protocolo e o
// Confirmar (409 garantia_vencida → confirma de novo), o "Mostrar: garantia acionada", o "SAC sem
// pedido", a aba Atendimento só com os casos confirmados (nome inteiro, "Aberto há N dias", Encerrar,
// Resumo do SAC e a resposta só por e-mail) e o Resumo sem o Status nem o "Ver todos".
// O contrato com o servidor: a parte do Python (schemas/rotas/ações do log) é conferida por inteiro,
// mas as diferenças só são contadas no FIM (as outras conferências rodam antes): enquanto o backend
// não tiver o contrato novo, o teste termina dizendo exatamente o que falta lá.
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
const scriptDe = (rel) => {
  const { descriptor } = parse(ler(rel))
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const P = scriptDe('../components/AtendimentoPlataforma.vue')
const E = scriptDe('../components/AtendimentoEmailResposta.vue')

// ─── harness: o <script setup> de verdade, com as auto-importações falsas ────
const icone = { render: () => Vue.h('i') }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => icone })
  if (nome === '@vueuse/core') return { onKeyStroke: () => {}, useMediaQuery: () => Vue.ref(true) }
  if (nome === '~/lib/garantias') return L
  if (nome === '~/components/AtendimentoPlataforma.vue') return P
  if (nome === '~/components/AtendimentoEmailResposta.vue') return E
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
const AUTO = ['ref', 'computed', 'reactive', 'watch', 'onMounted', 'onBeforeUnmount', 'nextTick', 'useApi', 'useToasts', 'useRoute', 'useRouter', 'useGarantiaAcesso', 'useAuthStore', 'definePageMeta', 'useCan']
function componente(rel, g) {
  const { codigo, render } = sfc(rel)
  const mod = { exports: {} }
  const valores = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: g.onMounted || (() => {}),
    onBeforeUnmount: g.onBeforeUnmount || (() => {}),
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
const VAZIOS = ['GarantiaStatus', 'GarantiaAparelho', 'GarantiaCobertura', 'GarantiaPrazoBarra', 'GarantiaDetalhe', 'GarantiaForm', 'GarantiaVincular', 'AtendimentoGarantia',
  'GarantiaHistorico', 'GarantiaSacSemPedido', 'GarantiaSacResumo', 'AtendimentoEmailResposta', 'AtendimentoIconePlataforma']
function appCom(C, props, g = {}, extras = {}) {
  const app = Vue.createSSRApp(C, props)
  for (const [n, c] of Object.entries(STUBS)) app.component(n, c)
  for (const n of VAZIOS) if (!extras[n]) app.component(n, { render: () => null })
  // Os de verdade (também os que não estão nos vazios, como o GarantiaResumo na página).
  for (const [n, rel] of Object.entries(extras)) app.component(n, componente(rel, g))
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
const REGISTRA_CONSULTA = { id: 'rc', role: 'user', permissions: { garantias: { view: true }, garantias_atendimento: { view: true, edit: true } } }

// O pedido da busca que ainda não tem garantia (04/11/2026; o exemplo da SPEC:
// o pedido do ML de antes de o robô começar). Sem CPF nem nome.
const PEDIDO_SEM_GARANTIA = {
  pedido_bling: '288113', pedido_marketplace: '2000017677253868', plataforma: 'ml', conta: 'Loja X', data: '2026-07-30T14:00:00Z',
  situacao: 'Entregue', produto_uranyx: true, motivo: 'antes_das_notas',
  motivo_texto: 'o pedido é de antes de 19/08/2026, quando o robô começou a criar a garantia pela NF.', pode_cadastrar: true,
}

// O caso do SAC do site na linha (06/11/2026): recebido (protocolo + Confirmar) e confirmado.
const SAC_RECEBIDO = { id: 31, protocolo: 'US-26-0031', recebido_em: '2026-11-03T13:00:00Z', confirmado_em: null, confirmado_por: null, encerrado_em: null, encerrado_motivo: null }
const SAC_ACIONADO = { ...SAC_RECEBIDO, id: 32, protocolo: 'US-26-0032', recebido_em: '2026-10-23T12:00:00Z', confirmado_em: '2026-10-24T14:00:00Z', confirmado_por: { id: 'u', nome: 'Ana' } }
const LINHA = {
  id: 7, cliente_nome: 'João Silva (joaozinho_88)', cpf_mascarado: '***.982.247-**', nf_numero: '10234', nf_serie: '1',
  pedido_bling: '55001', pedido_marketplace: '2000012345', plataforma: 'ml', conta: 'Loja X',
  data_inicio: '2026-10-07', fim_hardware: '2027-01-07', fim_software: '2027-10-07',
  status: 'ativa', status_rotulo: 'Ativa', entregue_sem_data: false, atendimentos: 2, criado_em: '2026-10-07T15:00:00Z',
  nome: 'João Silva', produto_nome: 'A17 pro max', produto_na_tabela: true, garantia_meses: 12, fim_garantia: '2027-10-07',
  status_pedido: 'Atendido', sac: SAC_RECEBIDO,
}
// As diferenças do lado do Python (o backend ainda sem o contrato): contadas no fim.
const pendentesPy = []
let camposTs_SacSemPedido = []
const conferirPy = (ok, msg) => { if (!ok) pendentesPy.push(msg) }
const mesmoPy = (a, b, msg) => {
  const [x, y] = [JSON.stringify(a), JSON.stringify(b)]
  if (x !== y) {
    const faltam = b.filter((c) => !a.includes(c))
    const sobram = a.filter((c) => !b.includes(c))
    pendentesPy.push(`${msg}${faltam.length ? ` — faltam: ${faltam.join(', ')}` : ''}${sobram.length ? ` — sobram: ${sobram.join(', ')}` : ''}${!faltam.length && !sobram.length ? ` — ordem: ${x} ≠ ${y}` : ''}`)
  }
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
  // Revisão 06/11/2026 (C8/C11): com o prazo do produto vale o fim ÚNICO, qualquer que seja o tipo (o
  // servidor faz a mesma conta); sem prazo cadastrado, o fim antigo do tipo.
  const gp = { ...g, fim_garantia: '2027-10-07' }
  assert.deepEqual(L.previaCobertura(gp, 'hardware', '2027-01-08T12:00:00Z'), { cobertura: 'coberto', fim: '2027-10-07', dia: '2027-01-08' }, 'o tipo não encurta a garantia')
  assert.deepEqual(L.previaCobertura(gp, null, '2027-01-08T12:00:00Z'), { cobertura: 'coberto', fim: '2027-10-07', dia: '2027-01-08' }, 'com o prazo, a prévia não espera o tipo')
  assert.equal(L.previaCobertura(gp, 'software', '2027-10-08T12:00:00Z').cobertura, 'fora_da_garantia', 'depois do fim único')
  assert.equal(L.previaCobertura({ ...g, fim_garantia: null }, null, '2027-01-08T12:00:00Z'), null, 'sem prazo e sem tipo: não dá para dizer')
  assert.deepEqual(L.previaCobertura({ ...g, fim_garantia: null }, 'hardware', '2027-01-08T12:00:00Z'), { cobertura: 'fora_da_garantia', fim: '2027-01-07', dia: '2027-01-08' }, 'sem prazo: o fim antigo do tipo')
  // O tipo é só a categoria: os rótulos sem hardware/software (o valor gravado continua o mesmo).
  assert.deepEqual(L.TIPOS_PROBLEMA.map((t) => [t.value, t.label]), [['hardware', 'Defeito no aparelho'], ['software', 'Sistema / aplicativo']])
  assert.ok(!L.TIPOS_PROBLEMA.some((t) => /hardware|software|\bHW\b|\bSW\b/i.test(`${t.label} ${t.dica}`)))
  assert.deepEqual(['hardware', 'software', 'outro', null].map(L.rotuloDoTipo), ['Defeito no aparelho', 'Sistema / aplicativo', 'outro', '—'])
  assert.equal(L.errosDaApi({ data: { detail: [{ loc: ['body', 'tipo_problema'], msg: "Input should be 'hardware' or 'software'" }] } }).campos.tipo_problema, 'Escolha o tipo do problema.')

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
  assert.match(ler('../components/GarantiaStatus.vue'), /sem_prazo: '[^']*dark:bg-orange-500\/15/, 'o "Prazo não cadastrado" com a cor do tema escuro')
  // Status com a cor do documento, sem emoji (29/10/2026: "igual aos nossos painéis"); desde
  // 06/11/2026 pelo prazo único — o "Somente software" saiu, entrou o "Prazo não cadastrado".
  assert.deepEqual(Object.fromEntries(Object.entries(L.STATUS).map(([k, v]) => [k, v.rotulo])), {
    aguardando_entrega: 'Aguardando entrega', ativa: 'Ativa', expirada: 'Expirada', sem_prazo: 'Prazo não cadastrado',
  })
  const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2B55}]/u
  const TELAS = ['../components/GarantiaStatus.vue', '../components/GarantiaCobertura.vue', '../components/GarantiaResumo.vue', '../components/GarantiaHistorico.vue', '../components/GarantiaAparelho.vue',
    '../components/GarantiaDetalhe.vue', '../components/GarantiaPrazoBarra.vue', '../components/GarantiaSacResumo.vue', '../components/GarantiaSacSemPedido.vue', '../pages/garantias.vue',
    // O modal "Registrar atendimento" (abre no cabeçalho do caso) e o bloco Garantia do Atendimento (revisão 06/11/2026).
    '../components/GarantiaVincular.vue', '../components/AtendimentoGarantia.vue']
  for (const f of ['../lib/garantias.ts', ...TELAS]) {
    const codigo = ler(f).split('\n').filter((l) => !l.trim().startsWith('//')).join('\n')
    assert.ok(!EMOJI.test(codigo), `${f}: sem emoji na tela`)
  }
  // Sem hardware/software no painel (06/11/2026): nem no template, nem nos rótulos da lib que a tela mostra.
  for (const f of TELAS) {
    const { descriptor } = parse(ler(f))
    assert.ok(!/hardware|software|Fim HW|Fim SW|\bHW\b|\bSW\b|fim_hardware|fim_software|meses_hardware|meses_software/i.test(descriptor.template.content), `${f}: sem hardware/software na tela`)
  }
  for (const t of [...Object.values(L.STATUS).flatMap((x) => [x.rotulo, x.dica]), ...L.FILTROS_STATUS.map((x) => x.label), ...L.CARTOES.flatMap((c) => [c.rotulo, c.dica]), ...Object.values(L.AVISOS)]) {
    assert.ok(!/hardware|software|\bHW\b|\bSW\b/i.test(t), `sem hardware/software: ${t}`)
  }
  // O "Aguardando" de pedido que o Bling já dá como entregue: outro texto, mesmo status.
  assert.equal(L.statusInfo('aguardando_entrega', true).rotulo, 'Entregue — sem data no DaVinci')
  assert.equal(L.statusInfo('aguardando_entrega').rotulo, 'Aguardando entrega')
  assert.equal(L.statusInfo('ativa', true).rotulo, 'Ativa', 'só vale para o aguardando')
  assert.deepEqual(L.FILTROS_STATUS.map((f) => [f.value, f.label]), [['todos', 'Todos'], ['ativa', 'Ativa'], ['expirada', 'Expirada'], ['aguardando_entrega', 'Aguardando entrega'],
    ['entregue_sem_data', 'Entregue — sem data no DaVinci'], ['vence_30d', 'Vence em 30 dias'], ['sem_prazo', 'Sem prazo cadastrado']])
  assert.equal(L.statusInfo('sem_prazo').rotulo, 'Prazo não cadastrado')
  // O nome sem o apelido do fim (a mesma regra do servidor), inclusive o cortado sem ")".
  assert.equal(L.nomeSemApelido('Maria Souza (maria123)'), 'Maria Souza')
  assert.equal(L.nomeSemApelido('Maria Souza (maria12'), 'Maria Souza', 'o cortado sem o ")"')
  assert.equal(L.nomeSemApelido('Maria Souza  ( ) '), 'Maria Souza')
  assert.equal(L.nomeSemApelido('Maria (Souza) Lima'), 'Maria (Souza) Lima', 'só o do fim')
  assert.equal(L.nomeSemApelido('(maria123)'), '(maria123)', 'sobrou vazio: o original')
  assert.equal(L.nomeSemApelido(null), '')
  assert.equal(L.nomeDaLinha({ cliente_nome: 'João Silva (jj)', nome: 'João da Silva' }), 'João da Silva', 'o do servidor vale')
  assert.equal(L.nomeDaLinha({ cliente_nome: 'João Silva (jj)' }), 'João Silva', 'sem o do servidor, a regra aqui')
  // O caso do SAC: sem caso, recebido (Confirmar) e acionado; o tempo em aberto (o dia em São Paulo).
  assert.deepEqual([null, SAC_RECEBIDO, SAC_ACIONADO].map(L.situacaoDoSac), ['sem', 'recebido', 'acionada'])
  assert.equal(L.tempoEmAberto(SAC_ACIONADO, '2026-11-04'), 'Aberto há 12 dias')
  assert.equal(L.tempoEmAberto({ recebido_em: '2026-11-04T02:00:00Z', encerrado_em: null }, '2026-11-04'), 'Aberto há 1 dia', '23h de 03/11 em SP')
  assert.equal(L.tempoEmAberto({ recebido_em: '2026-11-04T12:00:00Z', encerrado_em: null }, '2026-11-04'), 'Aberto hoje')
  assert.equal(L.tempoEmAberto({ recebido_em: '2026-10-23T12:00:00Z', encerrado_em: '2026-10-30T12:00:00Z' }, '2026-11-04'), 'Ficou aberto 7 dias', 'o encerrado para de contar')
  assert.equal(L.tempoEmAberto(null), '')
  // O prazo único: a barra (a conta do servidor, services/garantia.prazo_info) e os meses.
  assert.deepEqual(L.prazoUnico({ data_inicio: '2026-11-30', fim_garantia: '2027-02-28' }, '2026-12-01'), { fim: '2027-02-28', dias_total: 90, dias_restantes: 89, coberto_hoje: true })
  assert.deepEqual(L.prazoUnico({ data_inicio: '2026-11-30', fim_garantia: '2027-02-28' }, '2027-02-28'), { fim: '2027-02-28', dias_total: 90, dias_restantes: 0, coberto_hoje: true }, 'o último dia é coberto')
  assert.equal(L.prazoUnico({ data_inicio: '2026-11-30', fim_garantia: '2027-02-28' }, '2027-03-01').coberto_hoje, false)
  assert.equal(L.prazoUnico({ data_inicio: '2026-11-30', fim_garantia: null }, '2026-12-01'), null, 'sem prazo, sem barra')
  assert.equal(L.prazoUnico({ data_inicio: null, fim_garantia: null }, '2026-12-01'), null)
  assert.deepEqual([L.textoMeses(12), L.textoMeses(1), L.textoMeses(null)], ['12 meses', '1 mês', '—'])

  // Lista: o corpo do POST /lista (a busca nunca vai na URL) e os cartões que filtram.
  const f0 = { ...L.FILTROS_PADRAO }
  assert.deepEqual(L.corpoDaLista(f0), { limite: 100, offset: 0 })
  assert.deepEqual(L.corpoDaLista({ ...f0, busca: ' João ', status: 'expirada', periodo: 'entrega', de: '2026-01-01', ate: '', com_atendimento_no_mes: true }, 100),
    { limite: 100, offset: 100, busca: 'João', status: 'expirada', periodo: 'entrega', de: '2026-01-01', com_atendimento_no_mes: true })
  // Resumo por departamento (28/10/2026): o corpo do POST.
  assert.equal(L.largura(1, 5992), 1.5, 'o que não é zero aparece')
  assert.equal(L.largura(0, 10), 0)
  assert.equal(L.numeroBR(5992), (5992).toLocaleString('pt-BR'))
  assert.deepEqual(L.corpoDoResumo({ ...f0, busca: '306200', ordem: 'atividade', com_conversa: true }), {}, 'a busca e o "só com conversa" são da lista')
  assert.deepEqual(L.corpoDoResumo({ ...f0, status: 'ativa', periodo: 'entrega', de: '2026-10-01', ate: '', origem: 'robo' }),
    { periodo: 'entrega', de: '2026-10-01', origem: 'robo' }, 'o Status saiu do Resumo (06/11/2026): o da aba Garantia não vai junto')
  // O "Mostrar": "todas" é o padrão do servidor; só a "garantia acionada" vai no corpo.
  assert.equal(L.FILTROS_PADRAO.mostrar, 'todas')
  assert.deepEqual(L.corpoDaLista({ ...f0, mostrar: 'acionada' }), { limite: 100, offset: 0, mostrar: 'acionada' })
  assert.deepEqual(L.corpoDaLista({ ...f0, mostrar: 'todas' }), { limite: 100, offset: 0 })
  // As 3 partes (04/11/2026): os cartões ficam na aba Garantia — o "Atendimentos
  // no mês" não vai para o Resumo (lá ninguém vê o cartão ligado); nem a
  // pílula, nem o produto, nem o Atendimento.
  assert.deepEqual(L.corpoDoResumo({ ...f0, com_atendimento_no_mes: true, departamento: 'eletro', produto: 'tabela:1', em_atendimento: true, ids: [1] }), {})
  // A % do Resumo: gasto ÷ saldo × 100, 2 casas com vírgula; sem saldo, "—".
  assert.equal(L.pctDoGasto(229.4, 6650), '3,45%')
  assert.equal(L.pctDoGasto(0, 1000), '0,00%')
  assert.equal(L.pctDoGasto(12345.6, 100), '12.345,60%')
  assert.equal(L.pctDoGasto(5, -100), '-5,00%', 'saldo negativo: a conta como ela é')
  assert.equal(L.pctDoGasto(0, -100), '0,00%', 'nunca "-0,00%"')
  assert.equal(L.pctDoGasto(10, 0), '—')
  assert.equal(L.pctDoGasto(null, null), '—')
  // O corpo da lista: o departamento da pílula, o produto da linha do Resumo e
  // a aba Atendimento.
  assert.deepEqual(L.corpoDaLista({ ...f0, departamento: 'mala', produto: 'tabela:1', em_atendimento: true }),
    { limite: 100, offset: 0, departamento: 'mala', produto: 'tabela:1', em_atendimento: true })
  assert.deepEqual(L.corpoDaLista({ ...f0, departamento: null, produto: null, em_atendimento: false }), { limite: 100, offset: 0 })
  // Os departamentos (a ordem fixa da SPEC) e a aba/departamento da URL: o
  // Resumo é só do admin.
  assert.deepEqual(L.DEPARTAMENTOS.map((d) => [d.chave, d.nome]), [['celular', 'Celular'], ['mala', 'Mala'], ['eletro', 'Eletro']])
  assert.equal(L.abaDaUrl(undefined, true), 'resumo', 'o admin abre no Resumo')
  assert.equal(L.abaDaUrl(undefined, false), 'garantia', 'os outros, na Garantia')
  assert.equal(L.abaDaUrl('resumo', false), 'garantia', 'quem não é admin nunca cai no Resumo')
  assert.equal(L.abaDaUrl('resumo', true), 'resumo')
  assert.equal(L.abaDaUrl('atendimento', false), 'atendimento')
  assert.equal(L.abaDaUrl(['garantia'], true), 'garantia', 'a query repetida vale a 1ª')
  assert.equal(L.abaDaUrl('xyz', true), 'resumo')
  assert.equal(L.departamentoDaUrl('mala'), 'mala')
  assert.equal(L.departamentoDaUrl(['eletro', 'mala']), 'eletro')
  assert.equal(L.departamentoDaUrl('acessorios'), 'celular', 'o que não é departamento vira o Celular')
  assert.equal(L.departamentoDaUrl(undefined), 'celular')
  // A coluna Loja: plataforma · conta.
  assert.equal(L.lojaDaGarantia({ plataforma: 'ml', conta: 'Loja X' }), 'Mercado Livre · Loja X')
  assert.equal(L.lojaDaGarantia({ plataforma: 'shopee', conta: null }), 'Shopee')
  assert.equal(L.lojaDaGarantia({ plataforma: null, conta: null }), '—')
  // O selo da aba Atendimento: o motivo e há quanto tempo (o dia em São Paulo).
  assert.equal(L.haQuantoTempo('2026-11-01T15:00:00Z', '2026-11-04'), 'há 3 dias')
  assert.equal(L.haQuantoTempo('2026-11-03T15:00:00Z', '2026-11-04'), 'há 1 dia')
  assert.equal(L.haQuantoTempo('2026-11-04T02:00:00Z', '2026-11-04'), 'há 1 dia', '23h de 03/11 em SP')
  assert.equal(L.haQuantoTempo('2026-11-04T12:00:00Z', '2026-11-04'), 'hoje')
  assert.equal(L.haQuantoTempo(null, '2026-11-04'), '')
  assert.equal(L.seloDoAtendimento({ motivo: 'no_tecnico', rotulo: 'No técnico · vencido', desde: '2026-10-03T12:00:00Z', vencido: true }, '2026-11-04'), 'No técnico · há 32 dias · prazo vencido')
  assert.equal(L.seloDoAtendimento({ motivo: 'plataforma_decidindo', rotulo: 'Plataforma decidindo', desde: null, vencido: false }, '2026-11-04'), 'Plataforma decidindo')
  // O pedido que a busca achou sem garantia (nunca CPF nem nome).
  assert.equal(L.textoPedidoSemGarantia(PEDIDO_SEM_GARANTIA),
    'Pedido 288113 · Mercado Livre Loja X · nº na plataforma 2000017677253868 · compra 30/07/2026 · Entregue — ainda sem garantia: o pedido é de antes de 19/08/2026, quando o robô começou a criar a garantia pela NF.')
  assert.equal(L.textoPedidoSemGarantia({ ...PEDIDO_SEM_GARANTIA, pedido_marketplace: null, plataforma: null, conta: null, data: null, situacao: null, motivo: 'outro', motivo_texto: 'não achei o motivo.' }),
    'Pedido 288113 — ainda sem garantia: não achei o motivo.')
  assert.ok(L.ERROS.so_admin, 'o 403 do Resumo tem frase')
  // Pedido de garantia (28/10): a data do pedido é o dia em São Paulo (às
  // 22h30 de 27/10 em SP já é 28/10 em UTC).
  assert.equal(L.rotuloDoPedido({ pedido_em: '2026-10-28T01:30:00Z', pedido_origem: 'mensagem', nao_e_pedido: false }), 'Mensagem do cliente · 27/10/2026')
  assert.equal(L.rotuloDoPedidoComTrecho({ pedido_em: '2026-10-28T01:30:00Z', pedido_origem: 'mensagem', nao_e_pedido: false, pedido_trecho: 'o celular não liga' }), '“o celular não liga” · 27/10/2026')
  assert.equal(L.rotuloDoPedidoComTrecho({ pedido_em: '2026-10-28T01:30:00Z', pedido_origem: 'devolucao', nao_e_pedido: false, pedido_trecho: 'devolução: Danificado (Outros)' }), 'Devolução: Danificado (Outros) · 27/10/2026')
  assert.equal(L.rotuloDoPedido({ pedido_em: null, pedido_origem: null, nao_e_pedido: false }), 'sem pedido de garantia')
  assert.equal(L.rotuloDoPedido({ pedido_em: '2026-10-28T01:30:00Z', pedido_origem: 'mensagem', nao_e_pedido: true }), 'não é pedido de garantia')
  // SAC do site: ":" entre o rótulo e o valor colados (só na tela). O
  // formulário põe uma linha em branco entre os campos; cada rótulo vale uma
  // vez e só abrindo parágrafo — a descrição do cliente fica como ele escreveu.
  // O CPF sai mascarado (06/11/2026: "como no resto do painel").
  assert.equal(L.formularioSacLegivel('US-26-0009\n\n1. Dados do comprador\n\nCPF529.982.247-25\n\n2. Dados da compra\n\nNúmero da nota fiscal (se disponível)—\n\nNúmero do pedido: 123456'),
    'US-26-0009\n\n1. Dados do comprador\nCPF: ***.982.247-**\n\n2. Dados da compra\nNúmero da nota fiscal (se disponível): —\nNúmero do pedido: 123456')
  assert.equal(L.formularioSacLegivel('CPF: 52998224725'), 'CPF: ***.982.247-**')
  assert.ok(!/529\.982\.247-25/.test(L.formularioSacLegivel('CPF529.982.247-25\n\nNúmero do pedido261027QC4PXFRR')), 'nunca o CPF completo no cartão do formulário')
  assert.equal(L.formularioSacLegivel('CPF529.982.247-25', false), 'CPF: 529.982.247-25', 'sem a máscara só quando pedir')
  assert.equal(L.mascararCpfNoTexto('o 529.982.247-25 e o 11122233344'), 'o ***.982.247-** e o ***.222.333-**')
  assert.equal(L.formularioSacLegivel('CorPreto\r\n\r\nModeloT8\n\nDescrição detalhada do defeitoA tela apaga.\nCorreios entregou amassada.\nModelo antigo não fazia isso.\n\nCor da tela amarelada.'),
    'Cor: Preto\nModelo: T8\nDescrição detalhada do defeito: A tela apaga.\nCorreios entregou amassada.\nModelo antigo não fazia isso.\nCor da tela amarelada.')
  // Garantia automática (28/10/2026): ordem, "só com conversa" e origem.
  assert.deepEqual(L.corpoDaLista({ ...f0, ordem: 'atividade', com_conversa: true, origem: 'robo' }),
    { limite: 100, offset: 0, ordem: 'atividade', com_conversa: true, origem: 'robo' })
  assert.deepEqual(L.corpoDaLista({ ...f0, ordem: 'cadastro', com_conversa: false, origem: 'todas' }), { limite: 100, offset: 0 })
  assert.deepEqual(L.corpoDaLista({ ...f0, busca: '306200', ordem: 'atividade', com_conversa: true }), { limite: 100, offset: 0, busca: '306200', ordem: 'atividade' }, 'a busca procura também sem conversa')
  // O histórico: foto, vídeo (toca na tela), cartão e o anexo do ML sem link.
  const pecas = L.pecasDoHistorico({ id: 'm1', anexos: [
    { tipo: 'imagem', url: 'https://img.sp.mms.shopee.sg/a' },
    { tipo: 'video', url: 'https://v.vod.susercontent.com/b' },
    { url: 'https://x.tiktokcdn.com/c.mp4' },
    { tipo: 'pedido', pedido: '1' },
    { tipo: 'imagem', nome: 'foto.jpg' },
    { tipo: 'imagem', url: 'javascript:alert(1)' },
  ] })
  assert.deepEqual(pecas.map((x) => x.tipo), ['imagem', 'video', 'video', 'cartao', 'nome', 'nome'])
  const hist = {
    garantia_id: 1, restrito: false, desde: null, cortadas: 0,
    conversas: [{ id: 'c1', plataforma: 'shopee', canal: 'chat' }, { id: 'c2', plataforma: 'ml', canal: 'pos_venda' }],
    mensagens: [
      { id: 'a', conversa_id: 'c1', autor: 'cliente', origem: 'cliente', tipo: 'texto', texto: 'não liga', anexos: [], enviada_em: '2026-10-10T15:00:00Z' },
      { id: 'b', conversa_id: 'c2', autor: 'loja', origem: 'externo', tipo: 'texto', texto: 'ok', anexos: [], enviada_em: '2026-10-11T15:00:00Z' },
    ],
    marcos: [{ tipo: 'entrega', em: '2026-10-05T18:00:00Z', texto: 'Entregue', atendimento_id: null }],
  }
  assert.deepEqual(L.linhasDoHistorico(hist, Date.parse('2026-10-11T18:00:00Z')).map((x) => x.tipo === 'dia' ? `dia:${x.texto}` : x.tipo),
    ['dia:05/10/2026', 'marco', 'dia:Ontem', 'conversa', 'msg', 'dia:Hoje', 'conversa', 'msg'])
  assert.deepEqual(['cliente', 'loja', 'mediador', 'equipe', 'sistema'].map((a) => L.ladoDaMensagem({ autor: a, origem: a })),
    ['cliente', 'loja', 'mediador', 'nota', 'sistema'])
  const f1 = L.filtroDoCartao(f0, 'ativas')
  assert.equal(f1.status, 'ativa')
  assert.equal(L.cartaoAtivo(f1), 'ativas')
  assert.equal(L.filtroDoCartao(f1, 'ativas').status, 'todos', 'clicar de novo tira o filtro')
  const f2 = L.filtroDoCartao(f1, 'atendimentos_no_mes')
  assert.deepEqual([f2.status, f2.com_atendimento_no_mes], ['todos', true])
  assert.deepEqual([L.filtroDoCartao(f2, 'vence_30d').status, L.filtroDoCartao(f2, 'vence_30d').com_atendimento_no_mes], ['vence_30d', false])
  assert.equal(L.cartaoAtivo(L.filtroDoCartao(f0, 'sem_prazo')), 'sem_prazo')
  assert.equal(L.filtroDoCartao(L.filtroDoCartao(f0, 'sem_prazo'), 'sem_prazo').status, 'todos')
  assert.deepEqual(L.CARTOES.map((c) => [c.chave, c.rotulo]), [['ativas', 'Ativas'], ['vence_30d', 'Vence em 30 dias'], ['sem_prazo', 'Sem prazo cadastrado'], ['atendimentos_no_mes', 'Atendimentos no mês']], 'os 4 de 06/11/2026')
  // Os erros do SAC têm frase.
  for (const c of ['sac_sem_garantia', 'garantia_vencida', 'sac_nao_confirmado', 'sac_ja_confirmado', 'sac_outra_pessoa']) assert.ok(L.ERROS[c], `frase para ${c}`)
  // O vincular com outra pessoa na conversa (revisão 06/11/2026, C6): o 409 vira frase na tela.
  assert.match(L.errosDaApi({ statusCode: 409, data: { detail: { code: 'sac_outra_pessoa' } } }).geral, /^Outra pessoa escreveu na conversa deste SAC/)
  // O Confirmar com a garantia vencida grava o fim no log (campo garantia_vencida): tem rótulo.
  assert.equal(L.CAMPOS_LOG.garantia_vencida, 'Confirmou com a garantia já vencida (fim)')
  // O caso do servidor (SacOut) vira só o da linha.
  assert.deepEqual(L.sacDaLinha({ ...SAC_ACIONADO, garantia_id: 9, conversa_id: 'c', vinculo: 'pedido', encerrado_por: null }), SAC_ACIONADO)

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
  // O Resumo (a garantia por produto, com os reais): só admin (04/11/2026).
  assert.deepEqual(L.acessoGarantia(ADMIN), { consulta: true, cadastra: true, registra: true, veCpf: true, ve: true, log: true, resumo: true })
  assert.deepEqual(L.acessoGarantia(CONSULTA), { consulta: true, cadastra: false, registra: false, veCpf: false, ve: true, log: false, resumo: false })
  assert.deepEqual(L.acessoGarantia(CADASTRA), { consulta: true, cadastra: true, registra: false, veCpf: false, ve: true, log: false, resumo: false })
  assert.deepEqual(L.acessoGarantia(REGISTRA), { consulta: false, cadastra: false, registra: true, veCpf: false, ve: true, log: false, resumo: false })
  assert.deepEqual(L.acessoGarantia(COMUM), { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false, resumo: false })
  assert.equal(L.acessoGarantia({ role: 'user', permissions: { garantias_cpf: { view: true } } }).veCpf, true)
  assert.equal(L.acessoGarantia(null).ve, false)
  // Só o `view` de "Registrar atendimento": não vê nada (o backend também recusa).
  assert.deepEqual(L.acessoGarantia({ role: 'user', permissions: { garantias_atendimento: { view: true } } }), { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false, resumo: false })
  assert.equal(L.acessoGarantia(null).resumo, false)
}

// ═══════════════════════════════════════════════════════════════ contrato com o backend
{
  const schemas = api('schemas/garantia.py')
  const servico = api('services/garantia.py')
  const rotas = api('routers/garantias.py')
  const blocoPy = (classe) => {
    const ini = schemas.indexOf(`\nclass ${classe}(`)
    if (ini < 0) return null
    const fim = schemas.indexOf('\n\n\nclass ', ini + 1)
    return schemas.slice(ini + 1, fim < 0 ? undefined : fim)
  }
  const camposPy = (classe) => {
    const bloco = blocoPy(classe)
    assert.ok(bloco, `classe ${classe}`)
    const base = /^class \w+\((\w+)\):/.exec(bloco)[1]
    const proprios = [...bloco.matchAll(/^ {4}(\w+): /gm)].map((x) => x[1]).filter((n) => n !== 'model_config')
    return base === 'BaseModel' ? proprios : [...camposPy(base), ...proprios]
  }
  // A classe de um campo ("atendimento: AtendimentoAgoraOut | None" → a classe).
  const tipoPy = (classe, campo) => (new RegExp(`^ {4}${campo}: (?:list\\[|dict\\[str, )?(\\w+)`, 'm').exec(blocoPy(classe) || '') || [])[1]
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
  // Um campo do Python quando a classe existe (a classe que falta vira pendência, não erro aqui).
  const camposPyOu = (classe) => (blocoPy(classe) ? camposPy(classe) : null)
  for (const [py, tsTipo] of [
    ['PrazoOut', 'Prazo'], ['EntregaOut', 'Entrega'], ['ItemOut', 'ItemPedido'], ['AnexoOut', 'AnexoGarantia'],
    ['AtendimentoOut', 'AtendimentoGarantia'], ['PermissoesOut', 'PermissoesGarantia'],
    ['LogOut', 'LogLinha'], ['NotaSugeridaOut', 'NotaDoPedido'], ['PedidoParaCadastroOut', 'PedidoParaCadastro'],
    ['VinculoOut', 'Vinculo'], ['SituacaoConversaOut', 'SituacaoConversa'], ['RegrasOut', 'Regras'], ['SacOut', 'SacOut'],
  ]) {
    const doPy = camposPyOu(py)
    if (!doPy) conferirPy(false, `schemas/garantia.py: falta a classe ${py}`)
    else mesmoPy(doPy.sort(), camposTs(tsTipo).sort(), `${py} (Python) = ${tsTipo} (tela)`)
  }
  // O contrato, campo a campo como a SPEC manda (as 3 partes de 04/11/2026 + Garantia × SAC de
  // 06/11/2026). A tela é conferida contra esta lista aqui (quebra na hora); o servidor também, mas
  // as diferenças dele vão para `pendentesPy` (contadas no fim).
  const LINHA_ANTES = ['id', 'cliente_nome', 'cpf_mascarado', 'nf_numero', 'nf_serie', 'pedido_bling', 'pedido_marketplace', 'plataforma', 'conta',
    'data_inicio', 'fim_hardware', 'fim_software', 'status', 'status_rotulo', 'entregue_sem_data', 'atendimentos', 'criado_em', 'origem',
    'pedido_em', 'pedido_origem', 'pedido_origem_rotulo', 'nao_e_pedido', 'pedido_trecho', 'conversas', 'devolucao_situacao', 'devolucao_rotulo',
    'devolucao_vencido', 'devolucao_gasto', 'ultima_mensagem_em', 'ultima_previa', 'ultima_autor', 'ultima_plataforma']
  const SPEC = {
    ListaFiltro: ['busca', 'status', 'periodo', 'de', 'ate', 'com_atendimento_no_mes', 'ordem', 'com_conversa', 'origem', 'mostrar', 'ids', 'limite', 'offset',
      'departamento', 'produto', 'em_atendimento'],
    // 06/11/2026: o nome sem apelido, o produto da Tabela de Preços, o prazo único, o status do pedido no Bling e o SAC.
    GarantiaLinhaOut: [...LINHA_ANTES, 'departamento', 'atendimento', 'nome', 'produto_nome', 'produto_na_tabela', 'garantia_meses', 'fim_garantia', 'status_pedido', 'sac'],
    IndicadoresOut: ['ativas', 'vence_30d', 'sem_prazo', 'atendimentos_no_mes', 'acionadas', 'aguardando_entrega', 'entregue_sem_data', 'expiradas', 'total', 'vendidas'],
    GarantiaListaOut: ['itens', 'total', 'indicadores', 'hoje', 'abas', 'pedido_sem_garantia', 'sac_sem_pedido'],
    ResumoOut: ['vendidas', 'pediram', 'seguiram', 'hoje', 've_valores', 'departamentos', 'produtos'],
    ResumoDepartamentoOut: ['chave', 'nome', 'vendidas', 'pediram', 'saldo', 'gasto'],
    ResumoProdutoOut: ['chave', 'departamento', 'nome', 'skus', 'na_tabela', 'vendidas', 'pediram', 'seguiram', 'origens', 'consertos', 'perdidos',
      'gasto', 'gasto_conserto', 'gasto_perdidos', 'gasto_cliente', 'saldo', 'pedidos', 'pedidos_por_coluna'],
    SacDetalheOut: ['id', 'protocolo', 'recebido_em', 'conversa_id', 'confirmado_em', 'confirmado_por', 'encerrado_em', 'encerrado_por', 'encerrado_motivo', 'secoes', 'anexos'],
    SacSemPedidoOut: ['id', 'protocolo', 'recebido_em', 'nome', 'pedido_citado', 'nota_fiscal', 'modelo', 'local_compra', 'loja_vendedor'],
  }
  const SPEC_ANINHADOS = {
    // O objeto de dentro: [classe que o tem, campo] → os campos dele.
    atendimento: { de: ['GarantiaLinhaOut', 'atendimento'], ts: 'AtendimentoAgora', campos: ['motivo', 'rotulo', 'desde', 'vencido'] },
    pedido_sem_garantia: { de: ['GarantiaListaOut', 'pedido_sem_garantia'], ts: 'PedidoSemGarantia', campos: ['pedido_bling', 'pedido_marketplace', 'plataforma', 'conta', 'data', 'situacao', 'produto_uranyx', 'motivo', 'motivo_texto', 'pode_cadastrar'] },
    sac: { de: ['GarantiaLinhaOut', 'sac'], ts: 'SacDaLinha', campos: ['id', 'protocolo', 'recebido_em', 'confirmado_em', 'confirmado_por', 'encerrado_em', 'encerrado_motivo'] },
    secoes: { de: ['SacDetalheOut', 'secoes'], ts: 'SacSecao', campos: ['titulo', 'campos'] },
    campos: { de: ['SacSecaoOut', 'campos'], ts: 'SacCampo', campos: ['rotulo', 'valor'] },
    anexos: { de: ['SacDetalheOut', 'anexos'], ts: 'SacAnexo', campos: ['id', 'nome', 'tipo', 'tamanho', 'papel', 'url'] },
  }
  for (const [py, tsTipo] of [
    ['ListaFiltro', 'CorpoLista'], ['GarantiaLinhaOut', 'GarantiaLinha'], ['IndicadoresOut', 'Indicadores'], ['GarantiaListaOut', 'GarantiaLista'],
    ['ResumoOut', 'Resumo'], ['ResumoDepartamentoOut', 'ResumoDepartamento'], ['ResumoProdutoOut', 'ResumoProduto'],
    ['SacDetalheOut', 'SacDetalhe'], ['SacSemPedidoOut', 'SacSemPedido'],
  ]) {
    assert.deepEqual(camposTs(tsTipo).sort(), [...SPEC[py]].sort(), `${tsTipo} = SPEC de ${py}`)
  }
  for (const a of Object.values(SPEC_ANINHADOS)) assert.deepEqual(camposTs(a.ts).sort(), [...a.campos].sort(), `${a.ts} = SPEC`)
  assert.deepEqual(camposTs('SacSemPedidoLista').sort(), ['itens', 'total'])
  camposTs_SacSemPedido = camposTs('SacSemPedido')
  assert.deepEqual(camposTs('ContagemDaAba').sort(), ['atendimento', 'garantias'], 'abas: {garantias, atendimento} por departamento')
  // O "seguiram" saiu da tela (04/11/2026): nem nos indicadores, nem nas colunas. O "Só software" e o "HW vence" (06/11/2026) também.
  assert.ok(!camposTs('Indicadores').includes('seguiram'))
  assert.ok(!camposTs('Indicadores').some((c) => /software|hw_/.test(c)))
  // O servidor: as mesmas listas (as diferenças viram pendência).
  for (const py of Object.keys(SPEC)) {
    const doPy = camposPyOu(py)
    if (!doPy) conferirPy(false, `schemas/garantia.py: falta a classe ${py}`)
    else mesmoPy(doPy.sort(), [...SPEC[py]].sort(), `${py} = SPEC`)
  }
  for (const a of Object.values(SPEC_ANINHADOS)) {
    const nome = tipoPy(a.de[0], a.de[1])
    if (!blocoPy(a.de[0])) continue // a classe de fora já foi contada
    if (!nome) conferirPy(false, `schemas/garantia.py: ${a.de[0]}.${a.de[1]} não existe`)
    else if (blocoPy(nome)) mesmoPy(camposPy(nome).sort(), [...a.campos].sort(), `${nome} (${a.de[0]}.${a.de[1]}) = SPEC`)
  }
  // Os valores do status e do "Mostrar" (Literal no ListaFiltro) = os da tela.
  const literalPy = (classe, campo) => {
    const b = blocoPy(classe) || ''
    const linha = new RegExp(`^ {4}${campo}: ([^\\n=]+)`, 'm').exec(b)?.[1] || ''
    const alias = /^(\w+)\s*$/.exec(linha.trim())?.[1]
    const fonte = alias ? (new RegExp(`^${alias} = Literal\\[([\\s\\S]*?)\\]`, 'm').exec(schemas)?.[1] || '') : linha
    return [...fonte.matchAll(/"([a-z_0-9]+)"/g)].map((x) => x[1])
  }
  mesmoPy(literalPy('ListaFiltro', 'status').sort(), L.FILTROS_STATUS.map((f) => f.value).sort(), 'ListaFiltro.status = FILTROS_STATUS')
  mesmoPy(literalPy('ListaFiltro', 'mostrar').sort(), ['acionada', 'todas'], 'ListaFiltro.mostrar = todas | acionada')
  // O detalhe é a linha + o resto (GarantiaSalvaOut herda a GarantiaLinhaOut).
  if (blocoPy('GarantiaSalvaOut')) mesmoPy(camposPy('GarantiaSalvaOut').sort(), camposTs('GarantiaDetalhe').sort(), 'GarantiaSalvaOut = GarantiaDetalhe')
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
  // Os prazos fixos de antes ficam no servidor (a IA do Atendimento usa; a tela não mostra mais).
  assert.equal(String(L.REGRAS_PADRAO.meses_hardware), constante('MESES_HARDWARE'))
  assert.equal(String(L.REGRAS_PADRAO.meses_software), constante('MESES_SOFTWARE'))
  assert.equal(String(L.REGRAS_PADRAO.dias_alerta_hardware), constante('DIAS_ALERTA_HARDWARE'))
  assert.equal(String(L.REGRAS_PADRAO.dias_pausa_atendimento), constante('DIAS_PAUSA_NOVO_ATENDIMENTO'))
  assert.equal(`"${L.ENTREGUE_SEM_DATA.rotulo}"`, constante('ROTULO_ENTREGUE_SEM_DATA'))
  // O tipo do atendimento no log e no marco do histórico (servidor) = o rótulo do modal.
  {
    const bloco = /^TIPO_PROBLEMA_ROTULOS = \{([\s\S]*?)\n\}/m.exec(servico)
    assert.ok(bloco, 'TIPO_PROBLEMA_ROTULOS no servidor')
    const py = [...bloco[1].matchAll(/"(\w+)": "([^"]+)"/g)].map((x) => [x[1], x[2]])
    assert.deepEqual(py, L.TIPOS_PROBLEMA.map((t) => [t.value, t.label]))
    assert.ok(!/'Hardware' if a\.tipo_problema/.test(rotas), 'o marco do histórico sem Hardware/Software')
  }
  // Os rótulos de status são os do servidor.
  for (const [cod, info] of Object.entries(L.STATUS)) conferirPy(new RegExp(`: "${info.rotulo}",`).test(servico), `services/garantia.py: falta o rótulo "${info.rotulo}" (${cod})`)
  // Todo código de erro do router tem frase; todo aviso e motivo de anexo também.
  const codigos = [...new Set([...rotas.matchAll(/_erro\(\d+, "([a-z_]+)"/g), ...rotas.matchAll(/"code": "([a-z_]+)"/g)].map((m) => m[1]))]
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
  mesmoPy(acoesPy, ['cadastrou', 'alterou', 'consultou', 'registrou_atendimento', 'recalculou', 'buscou_cpf', 'criou_automatico', 'descartou', 'reativou', 'pedido_automatico', 'marcou_pedido', 'tirou_pedido',
    'confirmou_atendimento', 'encerrou_atendimento', 'vinculou_sac'], 'models/garantia.py GARANTIA_LOG_ACOES')
  for (const a of acoesPy) assert.ok(L.ACOES_LOG[a], `ação ${a}`)
  // Todo campo que o servidor grava no log tem rótulo (o log do admin não mostra o nome cru).
  const fontesDoLog = [rotas, servico, ...['services/garantia_prazo.py', 'services/garantia_sac.py', 'services/garantia_auto.py', 'services/garantia_pedido.py']
    .filter((f) => fs.existsSync(path.resolve(__dirname, '../../api/app', f))).map(api)]
  const camposDoLog = [...new Set(fontesDoLog.flatMap((f) => [...f.matchAll(/\bcampo="([a-z_]+)"/g)].map((m) => m[1])))]
  assert.ok(camposDoLog.includes('garantia_vencida') || !rotas.includes('ciente_vencida'), 'o campo do Confirmar vencido')
  for (const c of camposDoLog) assert.ok(L.CAMPOS_LOG[c], `rótulo do campo ${c} no log`)
  assert.deepEqual(Object.keys(L.ACOES_LOG).slice(-3), ['confirmou_atendimento', 'encerrou_atendimento', 'vinculou_sac'])
  // As rotas que a tela chama existem (lista e busca são POST: o termo vai no corpo).
  assert.ok(!rotas.includes('@router.get("",') && !rotas.includes('@router.get("/busca"'), 'nada de busca por GET')
  for (const r of ['@router.post("/lista"', '@router.get("/regras"', '@router.get("/pedido"', '@router.post("/busca"', '@router.get("/conversa/{conversa_id}"',
    '@router.post("",', '@router.get("/{garantia_id}"', '@router.put("/{garantia_id}"', '@router.post("/{garantia_id}/recalcular"',
    '"/{garantia_id}/atendimentos"', '@router.get("/{garantia_id}/log"', '@router.get("/anexos/{anexo_id}")']) {
    assert.ok(rotas.includes(r), `rota ${r}`)
  }
  // As rotas do SAC (06/11/2026) que a tela chama.
  for (const r of ['@router.post("/sac/{sac_id}/confirmar"', '@router.post("/sac/{sac_id}/encerrar"', '@router.get("/sac/sem-pedido"', '@router.post("/sac/{sac_id}/vincular"',
    '@router.get("/{garantia_id}/sac"', '"/sac/{sac_id}/anexos/{attachment_id}"']) {
    conferirPy(rotas.includes(r), `routers/garantias.py: falta a rota ${r}`)
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
  // As 3 partes (04/11/2026): o que o servidor manda — a lista da aba Garantia
  // (com os números das pílulas e das abas), a do Atendimento (em_atendimento)
  // e a busca que não acha garantia, mas acha o pedido.
  const ABAS_CONTAGEM = { celular: { garantias: 1234, atendimento: 7 }, mala: { garantias: 0, atendimento: 0 }, eletro: { garantias: 1, atendimento: 1 } }
  const LISTA = {
    // A 2ª venda: Shopee sem conta, produto fora da Tabela de Preços, sem prazo cadastrado e já com a garantia acionada.
    itens: [LINHA, { ...LINHA, id: 8, cliente_nome: 'Maria Souza (maria123)', nome: 'Maria Souza', cpf_mascarado: '***.111.222-**', nf_numero: '10198', nf_serie: '', plataforma: 'shopee', conta: null, data_inicio: '2026-06-02', fim_hardware: '2026-09-02', fim_software: '2027-06-02',
      status: 'sem_prazo', status_rotulo: 'Prazo não cadastrado', atendimentos: 0, produto_nome: 'Uranyx C57S AVULSO', produto_na_tabela: false, garantia_meses: null, fim_garantia: null, status_pedido: 'Excluído no Bling', sac: SAC_ACIONADO }],
    total: 2,
    indicadores: { ativas: 128, vence_30d: 17, sem_prazo: 5, atendimentos_no_mes: 23, acionadas: 3, aguardando_entrega: 4, entregue_sem_data: 3, expiradas: 9, total: 5992, vendidas: 5992 },
    hoje: '2026-11-04',
    abas: ABAS_CONTAGEM,
    pedido_sem_garantia: null,
    sac_sem_pedido: 2,
  }
  // A aba Atendimento (06/11/2026): só os casos do SAC confirmados e abertos, o mais antigo em cima; o motivo vira só o selo.
  const EM_ATENDIMENTO = {
    ...LISTA,
    itens: [
      { ...LINHA, id: 9, cliente_nome: 'Ana Maria de Albuquerque Cavalcanti Lima (aninha_cav)', nome: 'Ana Maria de Albuquerque Cavalcanti Lima', sac: SAC_ACIONADO, atendimento: { motivo: 'no_tecnico', rotulo: 'No técnico · vencido', desde: '2026-10-03T12:00:00Z', vencido: true }, devolucao_situacao: 'no_tecnico', devolucao_rotulo: 'No técnico · vencido há 2 dias', devolucao_vencido: true },
      { ...LINHA, id: 10, cliente_nome: 'Bruno Reis', nome: 'Bruno Reis', sac: { ...SAC_ACIONADO, id: 33, protocolo: 'US-26-0033', recebido_em: '2026-11-03T13:00:00Z' }, atendimento: { motivo: 'pedido_sem_resposta', rotulo: 'Pedido de garantia sem resposta', desde: '2026-11-03T15:00:00Z', vencido: false } },
    ],
    total: 2,
  }
  const VAZIA = { ...LISTA, itens: [], total: 0 }
  const RESUMO_PAGINA = { vendidas: 0, pediram: 0, seguiram: 0, hoje: '2026-11-04', ve_valores: true, departamentos: [], produtos: [] }
  const BUSCA_PEDIDO = '2000017677253868'
  let semGarantia = PEDIDO_SEM_GARANTIA
  // O Confirmar: a 1ª tentativa com a garantia vencida recebe o 409 (com o fim); a 2ª, ciente, passa.
  let vencida = false
  // O Encerrar (revisão 06/11/2026): a releitura da aba Atendimento já sem o caso 9; e a lista que
  // demora a voltar (para ver a linha mudar antes dela).
  let semCaso9 = false
  let segurarLista = null
  const responder = (url, opts = {}) => {
    if (/^\/api\/garantias\/sac\/\d+\/confirmar$/.test(url) && opts.method === 'POST') {
      if (vencida && !opts.body?.ciente_vencida) throw Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'garantia_vencida', fim_garantia: '2026-10-07' } } })
      return { ...SAC_RECEBIDO, confirmado_em: '2026-11-04T15:00:00Z', confirmado_por: { id: 'k', nome: 'Kátia' } }
    }
    if (url === '/api/garantias/lista' && opts.method === 'POST') {
      const b = opts.body
      if (segurarLista) return segurarLista
      if (b.departamento === 'mala') return VAZIA
      if (b.em_atendimento) return semCaso9 ? { ...EM_ATENDIMENTO, itens: EM_ATENDIMENTO.itens.filter((x) => x.id !== 9), total: 1 } : EM_ATENDIMENTO
      if (b.busca === BUSCA_PEDIDO) return { ...VAZIA, pedido_sem_garantia: semGarantia }
      return LISTA
    }
    if (url === '/api/garantias/resumo' && opts.method === 'POST') return RESUMO_PAGINA
    if (url === '/api/garantias/regras') return { ...L.REGRAS_PADRAO, status: {}, coberturas: {}, origens_entrega: {} }
    throw new Error(`chamada inesperada ${url}`)
  }
  const espera = (ms) => new Promise((r) => setTimeout(r, ms))
  // A página com a pessoa e a URL; as chamadas e os replace da URL de cada uma.
  function pagina(user, query = {}) {
    const chamadas = []
    const replaces = []
    const g = { user, api: async (url, opts = {}) => { chamadas.push({ url, opts }); return responder(url, opts) }, route: { query }, router: { replace: async (x) => replaces.push(x) } }
    const listas = () => chamadas.filter((c) => c.url === '/api/garantias/lista')
    return { g, chamadas, replaces, listas, corpo: () => listas().at(-1)?.opts.body, m: montar('../pages/garantias.vue', {}, g) }
  }
  // O middleware de permissão: Consultar.
  {
    const pg = sfc('../pages/garantias.vue')
    let meta
    new Function('definePageMeta', (pg.descriptor.scriptSetup.content.match(/definePageMeta\([^\n]*\)\n/) || [''])[0])((m) => { meta = m })
    assert.deepEqual(meta, { middleware: ['permission'], permission: { resource: 'garantias', action: 'view' } })
    // Sem o seletor "Visualização" nem a vista guardada no navegador (04/11/2026: as abas).
    const fonte = pg.descriptor.scriptSetup.content + pg.descriptor.template.content
    assert.ok(!/localStorage|garantias\.vista|Visualização|vistaDoSeletor/.test(fonte), 'as abas substituem o seletor de Visualização')
  }

  // ── quem não é admin: abre na Garantia; o Resumo não existe para ele ──
  {
    const p = pagina(CONSULTA)
    await p.m.montar()
    await espera(0)
    assert.equal(p.m.estado.aba, 'garantia')
    assert.equal(p.m.estado.departamento, 'celular', 'o Celular é o padrão')
    assert.deepEqual(p.m.estado.abasVisiveis.map((t) => t.chave), ['garantia', 'atendimento'])
    assert.equal(p.listas().length, 1, 'abrir a tela lê a lista uma vez')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular' }, 'a Garantia abre em todas as vendas com nota (o padrão do servidor: nada a mandar)')
    assert.equal(p.m.estado.mostrar, 'todas')
    assert.equal(p.replaces.length, 0, 'sem nada na URL, a URL fica como está')
    p.m.estado.trocarAba('resumo')
    await espera(0)
    assert.equal(p.m.estado.aba, 'garantia', 'nem trocando de aba')
    assert.equal(p.listas().length, 1)
    assert.ok(!p.chamadas.some((c) => c.url === '/api/garantias/resumo'))
  }
  // ?aba=resumo de quem não é admin cai na Garantia e a URL é corrigida (o
  // resto da query fica); ?dept= que não é departamento vira o Celular.
  {
    const p = pagina(CONSULTA, { aba: 'resumo', dept: 'acessorios', x: '1' })
    await p.m.montar()
    await espera(0)
    assert.equal(p.m.estado.aba, 'garantia')
    assert.equal(p.m.estado.departamento, 'celular')
    assert.deepEqual(p.replaces.at(-1).query, { aba: 'garantia', dept: 'celular', x: '1' })
  }
  // ?aba=atendimento&dept=eletro&garantia=7: abre ali, com o detalhe aberto.
  {
    const p = pagina(CADASTRA, { aba: 'atendimento', dept: 'eletro', garantia: '7' })
    await p.m.montar()
    await espera(0)
    assert.equal(p.m.estado.aba, 'atendimento')
    assert.equal(p.m.estado.departamento, 'eletro')
    assert.equal(p.m.estado.abertaId, 7, '?garantia= abre o detalhe')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'eletro', em_atendimento: true })
    assert.deepEqual(p.replaces.at(-1).query, { aba: 'atendimento', dept: 'eletro', garantia: '7' })
  }

  // ── o admin: abre no Resumo; cada troca relê a lista UMA vez (ou nenhuma) ──
  {
    const p = pagina(ADMIN)
    const { m } = p
    await m.montar()
    await espera(0)
    assert.equal(m.estado.aba, 'resumo', 'o admin abre no Resumo')
    assert.deepEqual(m.estado.abasVisiveis.map((t) => t.chave), ['resumo', 'garantia', 'atendimento'])
    assert.equal(p.listas().length, 1, 'no Resumo a lista vem uma vez: os números das pílulas e das abas')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular' }, 'no Resumo a lista leva só o que as pílulas precisam')
    assert.deepEqual(m.estado.abas, ABAS_CONTAGEM)
    assert.deepEqual(m.estado.contagem, { garantias: 1234, atendimento: 7 })
    // A pílula no Resumo: nada a reler (o Resumo traz os 3 departamentos); a URL guarda.
    m.estado.trocarDepartamento('eletro')
    await espera(0)
    assert.equal(p.listas().length, 1)
    assert.deepEqual(p.replaces.at(-1).query, { aba: 'resumo', dept: 'eletro' })
    assert.deepEqual(m.estado.contagem, { garantias: 1, atendimento: 1 })
    // A aba Garantia: uma leitura, do departamento da pílula.
    m.estado.trocarAba('garantia')
    await espera(0)
    assert.equal(p.listas().length, 2)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'eletro' })
    assert.deepEqual(p.replaces.at(-1).query, { aba: 'garantia', dept: 'eletro' })
    assert.equal(m.estado.emConversas, false, 'a Garantia é a Tabela')
    assert.equal(m.estado.selecionadaId, null, 'na Tabela nada fica aberto escondido')
    m.estado.filtros.status = 'expirada'
    await espera(0)
    assert.equal(p.listas().length, 3, 'o filtro da barra relê a lista uma vez')
    assert.equal(p.corpo().status, 'expirada')
    // O Atendimento: só quem está em atendimento agora — a barra não vale lá.
    m.estado.trocarAba('atendimento')
    await espera(0)
    assert.equal(p.listas().length, 4)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'eletro', em_atendimento: true })
    assert.equal(m.estado.emConversas, true)
    assert.equal(m.estado.selecionadaId, 9, 'na lista de Conversas a primeira já vem aberta (tela larga)')
    m.estado.trocarDepartamento('celular')
    await espera(0)
    assert.equal(p.listas().length, 5, 'a pílula relê uma vez')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular', em_atendimento: true })
    // De volta ao Resumo: nada a reler; o "limpar filtros" de lá também não relê a lista.
    m.estado.trocarAba('resumo')
    await espera(0)
    m.estado.limparFiltros()
    await espera(0)
    assert.equal(p.listas().length, 5)
    assert.equal(m.estado.filtros.status, 'todos')

    // A linha do Resumo abre a aba Garantia na lista de Conversas: todas as garantias do produto.
    m.estado.abrirProduto({ nome: 'A17 pro max', chave: 'tabela:1' })
    await espera(0)
    assert.equal(m.estado.aba, 'garantia')
    assert.equal(m.estado.abertoPor, 'produto')
    assert.equal(m.estado.produtoAberto, 'A17 pro max')
    assert.equal(m.estado.emConversas, true)
    assert.equal(p.listas().length, 6, 'abrir o produto: uma leitura')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, ordem: 'atividade', departamento: 'celular', produto: 'tabela:1' })
    // Um filtro da barra com o produto aberto: o produto fica (é filtro de verdade).
    m.estado.filtros.origem = 'robo'
    await espera(0)
    assert.equal(p.listas().length, 7)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, ordem: 'atividade', origem: 'robo', departamento: 'celular', produto: 'tabela:1' })
    m.estado.filtros.origem = 'todas'
    await espera(0)
    m.estado.voltarParaResumo()
    await espera(0)
    assert.equal(m.estado.aba, 'resumo')
    assert.equal(m.estado.abertoPor, null)
    assert.equal(m.estado.filtros.produto, null)
    // O número de uma coluna: só aqueles pedidos, com o aviso "produto · coluna".
    let n = p.listas().length
    m.estado.abrirProduto({ nome: 'A17 pro max', ids: [5, 6], coluna: 'Devolução' })
    await espera(0)
    assert.equal(m.estado.produtoAberto, 'A17 pro max · Devolução')
    assert.equal(p.listas().length, n + 1, 'abrir a célula: uma leitura')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, ordem: 'atividade', ids: [5, 6], departamento: 'celular' })
    // Os ids são uma foto: mexer na barra volta para a Tabela (o "todos os pedidos de garantia" saiu
    // em 06/11/2026) — na mesma releitura.
    m.estado.filtros.status = 'ativa'
    await espera(0)
    assert.equal(p.listas().length, n + 2)
    assert.equal(m.estado.abertoPor, null)
    assert.equal(m.estado.emConversas, false)
    assert.equal(m.estado.filtros.ids, undefined)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, status: 'ativa', departamento: 'celular' })
    // "voltar para a tabela": a Tabela da aba Garantia (relê uma vez).
    m.estado.voltarParaTabela()
    await espera(0)
    assert.equal(m.estado.abertoPor, null)
    assert.equal(p.listas().length, n + 3)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, status: 'ativa', departamento: 'celular' })
    m.estado.limparFiltros()
    await espera(0)
    assert.equal(p.listas().length, n + 4, 'limpar: uma leitura')
    assert.equal(m.estado.verTodosOsPedidos, undefined, 'o "ver todos os pedidos de garantia" saiu')
    // A busca filtra a Tabela: a lista de Conversas fecha — depois da pausa, uma leitura só, com o termo inteiro.
    n = p.listas().length
    m.estado.filtros.busca = 'm'
    await espera(50)
    m.estado.filtros.busca = 'maria'
    await espera(0)
    assert.equal(p.listas().length, n, 'nada sai enquanto digita')
    await espera(350)
    assert.equal(m.estado.abertoPor, null, 'a busca fecha a lista de Conversas')
    assert.equal(p.listas().length, n + 1, 'a busca: uma leitura só')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, busca: 'maria', departamento: 'celular' })
    m.estado.filtros.busca = ''
    await espera(350)
    // O "Mostrar": garantia acionada → uma leitura (com o mostrar); de volta a todas, sem ele.
    n = p.listas().length
    m.estado.mostrar = 'acionada'
    await espera(0)
    assert.equal(p.listas().length, n + 1, 'o Mostrar relê a Tabela uma vez')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, mostrar: 'acionada', departamento: 'celular' })
    m.estado.mostrar = 'todas'
    await espera(0)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular' })
    // A busca (nome/CPF) vai no corpo, nunca na URL.
    m.estado.filtros.busca = '529.982.247-25'
    await espera(350)
    assert.equal(p.corpo().busca, '529.982.247-25')
    assert.ok(p.chamadas.every((c) => !c.url.includes('?') && !c.opts.query), 'nada na query string')
    assert.ok(p.replaces.every((r) => !JSON.stringify(r).includes('529')), 'nem na URL da página')
    m.estado.filtros.busca = ''
    await espera(350)
    // Os cartões filtram; abrir a linha põe a garantia na URL.
    m.estado.clicarCartao('vence_30d')
    assert.equal(m.estado.filtros.status, 'vence_30d')
    await espera(0)
    assert.equal(p.corpo().status, 'vence_30d', 'o cartão relê a lista filtrada')
    m.estado.clicarCartao('sem_prazo')
    await espera(0)
    assert.equal(p.corpo().status, 'sem_prazo')
    m.estado.limparFiltros()
    assert.equal(m.estado.filtros.status, 'todos')
    await espera(0)
    // A pílula com o produto aberto: o produto é do outro departamento — fecha (a Tabela do novo).
    m.estado.abrirProduto({ nome: 'A17 pro max', chave: 'tabela:1' })
    await espera(0)
    n = p.listas().length
    m.estado.trocarDepartamento('eletro')
    await espera(0)
    assert.equal(p.listas().length, n + 1)
    assert.deepEqual([m.estado.abertoPor, m.estado.filtros.produto, m.estado.emConversas], [null, null, false])
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'eletro' })
    m.estado.abrir(7)
    await espera(0)
    assert.deepEqual(p.replaces.at(-1).query, { aba: 'garantia', dept: 'eletro', garantia: '7' })
  }

  // O que fica para trás na aba Garantia não vale no Resumo (revisão 04/11): o
  // CPF da busca (escondida lá) não volta no "atualizar" — contaria no teto e
  // no log —, e o cartão "Atendimentos no mês" não corta a lista que o Resumo abre.
  {
    const p = pagina(ADMIN, { aba: 'garantia' })
    const { m } = p
    await m.montar()
    await espera(0)
    m.estado.filtros.busca = '529.982.247-25'
    await espera(350)
    assert.equal(p.corpo().busca, '529.982.247-25', 'na Garantia a busca vale')
    m.estado.trocarAba('resumo')
    await espera(0)
    let n = p.listas().length
    await m.estado.carregar() // o "atualizar" (e o salvar/corrigir) no Resumo
    assert.equal(p.listas().length, n + 1)
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular' }, 'sem a busca escondida')
    assert.ok(p.listas().slice(n).every((c) => !JSON.stringify(c.opts.body).includes('529')), 'o CPF não volta no Resumo')
    assert.equal(m.estado.filtros.busca, '529.982.247-25', 'a busca continua lá para quando voltar à Garantia')
    m.estado.trocarAba('garantia')
    await espera(0)
    assert.equal(p.corpo().busca, '529.982.247-25')
    m.estado.filtros.busca = ''
    await espera(350)
    m.estado.clicarCartao('atendimentos_no_mes')
    await espera(0)
    assert.equal(p.corpo().com_atendimento_no_mes, true, 'o cartão filtra a Tabela')
    m.estado.trocarAba('resumo')
    await espera(0)
    await m.estado.carregar()
    assert.equal(p.corpo().com_atendimento_no_mes, undefined, 'nem o cartão no Resumo')
    n = p.listas().length
    m.estado.abrirProduto({ nome: 'A17 pro max', chave: 'tabela:1' })
    await espera(0)
    assert.equal(p.listas().length, n + 1, 'abrir o produto: uma leitura')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, ordem: 'atividade', departamento: 'celular', produto: 'tabela:1' }, 'a lista aberta é a do número do Resumo')
    assert.equal(m.estado.ativo, null, 'o cartão saiu')
    // O Status ligado na aba Garantia não vai para o Resumo (06/11/2026: o Resumo não tem Status).
    m.estado.voltarParaTabela()
    await espera(0)
    m.estado.filtros.status = 'expirada'
    await espera(0)
    assert.equal(p.corpo().status, 'expirada')
    assert.deepEqual(L.corpoDoResumo(m.estado.filtros), {}, 'o /resumo não leva o status da aba Garantia')
    m.estado.trocarAba('resumo')
    await espera(0)
    assert.equal(m.estado.temFiltro, false, 'no Resumo o status não conta como filtro')
    m.estado.filtros.de = '2026-11-01'
    await espera(0)
    assert.equal(m.estado.temFiltro, true, 'a data conta')
    // Nem na lista que o Resumo abre (revisão 06/11/2026): o número do Resumo não tem Status.
    m.estado.filtros.de = ''
    await espera(0)
    n = p.listas().length
    m.estado.abrirProduto({ nome: 'A17 pro max', chave: 'tabela:1' })
    await espera(0)
    assert.equal(p.listas().length, n + 1, 'abrir o produto: uma leitura')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, ordem: 'atividade', departamento: 'celular', produto: 'tabela:1' }, 'sem o status expirada da aba Garantia')
    assert.equal(m.estado.filtros.status, 'todos', 'o Status da barra mostra o que vale')
  }

  // A coluna Atendimento: Confirmar (sem e-mail) e o caso aberto na aba Atendimento.
  {
    const toastsVistos = []
    const p = pagina(REGISTRA_CONSULTA, { aba: 'garantia' })
    p.g.toasts = { success: (t, d) => toastsVistos.push(['ok', t, d]), error: (t, d) => toastsVistos.push(['erro', t, d]), info() {}, warning() {} }
    const m = montar('../pages/garantias.vue', {}, p.g)
    await m.montar()
    await espera(0)
    const n = p.listas().length
    let soltar
    segurarLista = new Promise((r) => { soltar = r })
    await m.estado.confirmar(LINHA)
    // A linha já fica "Garantia acionada" com a resposta do Confirmar (a lista ainda não voltou).
    assert.equal(L.situacaoDoSac(m.estado.itens.find((x) => x.id === 7).sac), 'acionada')
    assert.deepEqual(m.estado.itens.find((x) => x.id === 7).sac.confirmado_por, { id: 'k', nome: 'Kátia' })
    assert.equal(m.estado.itens.find((x) => x.id === 8).sac.id, 32, 'as outras linhas ficam')
    segurarLista = null
    soltar(LISTA)
    await espera(0)
    const conf = p.chamadas.filter((c) => c.url === '/api/garantias/sac/31/confirmar')
    assert.deepEqual(conf.map((c) => c.opts), [{ method: 'POST', body: { ciente_vencida: false } }])
    assert.deepEqual(toastsVistos.at(-1).slice(0, 2), ['ok', 'Garantia acionada — US-26-0031'])
    assert.match(toastsVistos.at(-1)[2], /Nenhum e-mail foi enviado/)
    await espera(0)
    assert.equal(p.listas().length, n + 1, 'confirmou: a lista relê')
    // A garantia vencida: o servidor recusa com o fim; a pessoa confirma sabendo, e vai de novo com ciente_vencida.
    vencida = true
    const perguntas = []
    const antes = globalThis.window
    try {
      globalThis.window = { confirm: (t) => { perguntas.push(t); return perguntas.length === 1 }, matchMedia: () => ({ matches: true }) }
      await m.estado.confirmar(LINHA)
      assert.deepEqual(perguntas, ['A garantia venceu em 07/10/2026. Confirmar mesmo assim?'])
      assert.deepEqual(p.chamadas.filter((c) => c.url === '/api/garantias/sac/31/confirmar').slice(1).map((c) => c.opts.body), [{ ciente_vencida: false }, { ciente_vencida: true }])
      // Desistiu: nada sai depois do 409.
      const k = p.chamadas.length
      await m.estado.confirmar(LINHA)
      assert.equal(perguntas.length, 2)
      assert.deepEqual(p.chamadas.slice(k).filter((c) => c.url.includes('/confirmar')).map((c) => c.opts.body), [{ ciente_vencida: false }], 'sem a 2ª tentativa')
    } finally {
      globalThis.window = antes
      vencida = false
    }
    assert.ok(!p.chamadas.some((c) => /responder|email/.test(c.url)), 'confirmar não manda e-mail')
    // "abrir o caso": a aba Atendimento já com esta garantia aberta (mesmo que ela não esteja na lista).
    const acionada = LISTA.itens[1]
    m.estado.abrirCaso(acionada)
    await espera(0)
    assert.equal(m.estado.aba, 'atendimento')
    assert.deepEqual(p.corpo(), { limite: 100, offset: 0, departamento: 'celular', em_atendimento: true })
    assert.equal(m.estado.selecionadaId, 8)
    assert.equal(m.estado.selecionada.id, 8, 'a linha da Tabela segura o caso aberto')
  }

  // Revisão 06/11/2026 (C7): o Encerrar (no cabeçalho do caso) devolve o caso encerrado e a linha aberta
  // passa a mostrá-lo assim na hora — sem o Encerrar e sem a caixa de e-mail —, mesmo com a releitura da
  // aba Atendimento já sem o caso; o histórico NÃO se relê por aqui (cada leitura grava "consultou").
  {
    const p = pagina(REGISTRA_CONSULTA, { aba: 'atendimento' })
    const { m } = p
    await m.montar()
    await espera(0)
    assert.equal(m.estado.selecionadaId, 9)
    const versao = m.estado.versaoHistorico
    const n = p.listas().length
    const ENCERRADO = { ...SAC_ACIONADO, encerrado_em: '2026-11-04T16:00:00Z', encerrado_motivo: 'pessoa' }
    semCaso9 = true
    try {
      m.estado.aoMudarCaso({ ...ENCERRADO, garantia_id: 9, conversa_id: 'conv-sac-32', vinculo: 'pedido', encerrado_por: { id: 'rc', nome: 'Rita' } })
      assert.deepEqual(m.estado.selecionada.sac, ENCERRADO, 'na hora, antes de a lista voltar')
      await espera(0)
      assert.equal(p.listas().length, n + 1, 'a lista relê uma vez')
      assert.deepEqual(m.estado.itens.map((x) => x.id), [10], 'o caso encerrado saiu da aba Atendimento')
      assert.equal(m.estado.selecionadaId, 9, 'e continua aberto no painel')
      assert.deepEqual(m.estado.selecionada.sac, ENCERRADO, 'já encerrado (o GarantiaHistorico tira o Encerrar e a resposta)')
      assert.equal(m.estado.versaoHistorico, versao, 'o histórico não é relido')
      // O "mudou" sem o caso (registrou atendimento): só a lista; o histórico se relê sozinho.
      m.estado.aoMudarCaso()
      await espera(0)
      assert.equal(p.listas().length, n + 2)
      assert.equal(m.estado.versaoHistorico, versao)
      assert.deepEqual(m.estado.selecionada.sac, ENCERRADO)
    } finally {
      semCaso9 = false
    }
  }

  // ?novo=<pedido>: só quem cadastra abre o formulário; o ?novo= sai da URL.
  for (const [u, abre] of [[CADASTRA, true], [CONSULTA, false]]) {
    const p = pagina(u, { novo: '55001' })
    await p.m.montar()
    assert.equal(p.m.estado.formAberto, abre, `${u.id}: ?novo abre o cadastro = ${abre}`)
    if (abre) assert.equal(p.m.estado.pedidoInicial, '55001')
    assert.equal(p.replaces.at(-1).query.novo, undefined)
  }

  // A tela renderizada (SSR não roda o onMounted: carrega à mão e renderiza de novo com o estado).
  const EXTRAS = { GarantiaStatus: '../components/GarantiaStatus.vue', GarantiaResumo: '../components/GarantiaResumo.vue' }
  const abrir = async (user, query = {}) => {
    const p = pagina(user, query)
    const cx = comEstado(componente('../pages/garantias.vue', p.g))
    await html(appCom(cx.C, {}, p.g, EXTRAS))
    await cx.estado.carregar()
    return { ...p, cx, h: async () => html(appCom(cx.mesmo, {}, p.g, EXTRAS)) }
  }
  const abertura = (h, attr) => [...h.matchAll(/<(?:button|div|span|th|p)\b[^>]*>/g)].map((x) => x[0]).filter((t) => t.includes(attr))
  const classe = (tag) => (/ class="([^"]*)"/.exec(tag || '') || [])[1] || ''
  // O admin no Resumo: as pílulas (com os números) e as abas, como na Tabela de Preços; o Resumo lê o /resumo.
  {
    const t = await abrir(ADMIN)
    const h = await t.h()
    assert.match(h, /Painel de Garantia — Uranyx/)
    assert.match(h, /data-nova-garantia/)
    const pilulas = [...h.matchAll(/<button\b[^>]*data-departamento="(\w+)"[^>]*>([\s\S]*?)<\/button>/g)].map((x) => [x[1], texto(x[2])])
    assert.deepEqual(pilulas, [['celular', 'Celular (1.234 garantias)'], ['mala', 'Mala (0 garantias)'], ['eletro', 'Eletro (1 garantia)']])
    const [celular, mala] = abertura(h, 'data-departamento=')
    assert.match(classe(celular), /\bbg-primary text-primary-foreground border-primary\b/, 'a pílula escolhida')
    assert.match(classe(mala), /\bborder-border hover:bg-muted\b/)
    assert.ok(!/bg-primary/.test(classe(mala)))
    assert.match(classe(abertura(h, 'data-abas')[0]), /^flex flex-wrap gap-1 rounded-md bg-muted\/40 p-1 w-fit$/, 'a barra de abas da Tabela de Preços')
    const abas = [...h.matchAll(/<button\b[^>]*data-aba="(\w+)"[^>]*>([\s\S]*?)<\/button>/g)].map((x) => [x[1], texto(x[2])])
    assert.deepEqual(abas, [['resumo', 'Resumo'], ['garantia', 'Garantia (1.234)'], ['atendimento', 'Atendimento (7)']])
    const tagsAba = abertura(h, 'data-aba=')
    assert.match(classe(tagsAba[0]), /\bbg-background shadow-sm\b/, 'a aba escolhida')
    assert.match(classe(tagsAba[1]), /\btext-muted-foreground hover:text-foreground\b/)
    assert.match(h, /data-garantia-resumo/, 'o Resumo na tela')
    assert.ok(t.chamadas.some((c) => c.url === '/api/garantias/resumo'), 'o admin lê o /resumo')
    assert.ok(!/id="gar-busca"/.test(h), 'a busca é da aba Garantia')
    assert.ok(!/id="gar-status"/.test(h), 'o Resumo não tem o filtro Status (06/11/2026)')
    assert.ok(!/data-barra/.test(h), 'nem a barra (só o período, ao lado das abas)')
    assert.ok(!/data-ver-todos|Ver todos os pedidos de garantia/.test(h), 'nem o "Ver todos os pedidos de garantia" (06/11/2026)')
    // O período ao lado das abas (05/11/2026), sempre pela data da venda: o
    // "Período por" saiu ("exclua isso também"); ficam só as datas.
    const linhaAbas = (h.match(/data-linha-abas[^>]*>([\s\S]*?)data-garantia-resumo/) || [])[1] || ''
    assert.match(linhaAbas, /data-abas[\s\S]*data-periodo[\s\S]*id="gar-de"[\s\S]*id="gar-ate"/, 'o período fica ao lado das abas')
    assert.ok(!/id="gar-periodo"|Período por/.test(h), 'sem o "Período por"')
    assert.ok(!/id="gar-origem"|data-origem/.test(h), 'sem a Origem (05/11/2026, "excluir origem também")')
    assert.match(linhaAbas, /<label for="gar-de"[^>]*>Vendas de<\/label>/)
    assert.equal(t.cx.estado.filtros.periodo, 'venda')
    assert.equal(L.FILTROS_PADRAO.periodo, 'venda')
    assert.ok(!/data-indicadores/.test(h), 'os cartões são da aba Garantia')
    assert.ok(!/seguiram/i.test(texto(h)), 'nada de "seguiram" na tela')
    // "Como funciona": as 3 partes, o Resumo só para admin, a %, o prazo do produto, o SAC e o Atendimento.
    t.cx.estado.regrasAbertas = true
    const hr = await t.h()
    const regras = texto(hr)
    for (const trecho of ['3 partes', 'Resumo (só para administradores)', 'só os produtos da Tabela de Preços › Produtos', 'A % é gasto ÷ saldo × 100',
      'saldo = base − comissão − frete − custo do produto, a conta da planilha de rentabilidade (o que sobrou)',
      'Com o período pela data da venda, tudo é só das vendas daqueles dias',
      'gasto = tela Reembolso (conserto + perdidos + reembolso ao cliente) de quem pediu garantia',
      'O fim da garantia = entrega + o prazo do produto na Tabela de Preços › Produtos (coluna Garantia, em meses)',
      'Se o prazo do produto aumentar, vale para todas as vendas dele; se diminuir, só para as vendas a partir da mudança',
      'Status: Ativa · Expirada · Aguardando entrega · Prazo não cadastrado',
      'pelo nº do pedido na plataforma, depois pela nota fiscal, depois pelo CPF + produto',
      'Confirmar não manda e-mail',
      'Atendimento = só os casos com Garantia acionada (o SAC confirmado), o mais antigo em cima',
      'A resposta ao cliente é só por e-mail']) {
      assert.ok(regras.includes(trecho), `Como funciona: ${trecho}`)
    }
    assert.ok(!/Seguiram ÷ Vendidas/.test(regras))
    assert.ok(!/hardware|software/i.test(texto((hr.match(/data-regras[^>]*>([\s\S]*?)<\/section>/) || [])[1] || '')), 'o "Como funciona" sem hardware/software')
    assert.ok(!/hardware|software/i.test(texto(hr.match(/<header[\s\S]*?<\/header>/)?.[0] || '')), 'o cabeçalho também')
  }
  // Quem não é admin: sem a aba Resumo e sem /resumo — nem com ?aba=resumo.
  {
    const t = await abrir(CONSULTA, { aba: 'resumo' })
    const h = await t.h()
    const abas = [...h.matchAll(/<button\b[^>]*data-aba="(\w+)"[^>]*>([\s\S]*?)<\/button>/g)].map((x) => [x[1], texto(x[2])])
    assert.deepEqual(abas, [['garantia', 'Garantia (1.234)'], ['atendimento', 'Atendimento (7)']])
    assert.ok(!/data-garantia-resumo/.test(h))
    assert.ok(!t.chamadas.some((c) => c.url === '/api/garantias/resumo'), 'quem não é admin não chama o /resumo')
    assert.ok(!/data-nova-garantia/.test(h), 'sem Cadastrar, sem o + Nova garantia')
  }
  // A aba Garantia: a barra, os cartões, a linha do resumo (sem "seguiram") e a Tabela com a Loja.
  {
    const t = await abrir(CADASTRA, { aba: 'garantia' })
    const h = await t.h()
    assert.match(h, /placeholder="Nome \/ CPF \/ NF \/ Pedido"/)
    assert.deepEqual([...h.matchAll(/<select id="gar-mostrar"[\s\S]*?<\/select>/g)].map((x) => [...x[0].matchAll(/<option[^>]*>([^<]*)</g)].map((o) => o[1])),
      [['todas as vendas com nota', 'garantia acionada']])
    assert.equal(t.cx.estado.mostrar, 'todas', 'o padrão da aba Garantia')
    assert.match(h, /id="gar-status"/, 'o Status fica na aba Garantia')
    // As 10 colunas de 06/11/2026, na ordem da especificação.
    const ths = [...h.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((x) => texto(x[1]))
    assert.deepEqual(ths, ['Nome', 'Pedido', 'Plataforma', 'Loja', 'Produto', 'Entrega', 'Garantia total', 'Garantia', 'Status do pedido', 'Atendimento'])
    const tabela = (h.match(/<table[^>]*data-tabela-garantias[\s\S]*?<\/table>/) || [])[0] || ''
    const tds = (k) => [...tabela.matchAll(new RegExp(`<td[^>]*data-${k}[^>]*>([\\s\\S]*?)</td>`, 'g'))].map((x) => texto(x[1]))
    assert.deepEqual(tds('nome'), ['João Silva', 'Maria Souza'], 'o nome sem o apelido')
    assert.deepEqual(tds('pedido-bling'), ['55001', '55001'])
    assert.deepEqual(tds('plataforma'), ['Mercado Livre', 'Shopee'])
    assert.deepEqual(tds('loja'), ['Loja X', '—'])
    assert.deepEqual(tds('produto'), ['A17 pro max', 'Uranyx C57S AVULSO fora da Tabela'], 'fora da Tabela de Preços: o nome da nota com o aviso')
    assert.deepEqual(tds('entrega'), ['07/10/26', '02/06/26'])
    assert.deepEqual(tds('garantia-total'), ['07/10/27', '—'])
    assert.deepEqual(tds('garantia-meses'), ['12 meses', '—'])
    assert.equal((tabela.match(/data-sem-prazo/g) || []).length, 2, 'sem prazo: o aviso nas duas colunas')
    assert.deepEqual(tds('status-pedido'), ['Atendido', 'Excluído no Bling'])
    assert.deepEqual(tds('atendimento-sac'), ['US-26-0031 03/11/2026', 'Garantia acionada abrir o caso'], 'sem "Registrar atendimento", sem o Confirmar')
    assert.ok(!/data-confirmar-sac/.test(tabela))
    assert.ok(!/data-status-garantia|cpf|\*\*\*\.982|10234|Fim HW|Fim SW|Pedido de garantia/i.test(tabela), 'saíram: Pedido de garantia, CPF, NF, Fim HW, Fim SW e o Status da garantia')
    assert.match(sfc('../pages/garantias.vue').descriptor.template.content, /data-confirmar-sac @click\.stop="confirmar\(g\)"/, 'o Confirmar não abre a linha')
    assert.ok(!/529\.982\.247-25|52998224725|\*\*\*\.982\.247-\*\*/.test(h), 'nem o CPF na lista')
    // Os cartões do celular: os mesmos dados (o Atendimento fora do botão da linha).
    const cel = (h.match(/<ul[^>]*data-cartoes-garantias[\s\S]*?<\/ul>/) || [])[0] || ''
    assert.deepEqual([...cel.matchAll(/<div[^>]*data-loja[^>]*>([\s\S]*?)<\/div>/g)].map((x) => texto(x[1])), ['pedido 55001 · Mercado Livre · Loja X', 'pedido 55001 · Shopee'])
    assert.deepEqual([...cel.matchAll(/<div[^>]*data-atendimento-sac[^>]*>([\s\S]*?)<\/div>/g)].map((x) => texto(x[1])), ['US-26-0031 03/11/2026', 'Garantia acionada abrir o caso'])
    assert.ok(!/<button[^>]*>(?:(?!<\/button>)[\s\S])*<button/.test(cel), 'nenhum botão dentro de outro')
    assert.ok(!/hardware|software|Fim HW|Fim SW/i.test(texto(h)), 'nada de hardware/software na aba Garantia')
    const cartoes = [...h.matchAll(/data-cartao="([a-z_0-9]+)"[\s\S]*?<\/button>/g)].map((x) => [x[1], texto(x[0].replace(/^[^>]*>/, ''))])
    assert.deepEqual(cartoes, [['ativas', 'Ativas 128'], ['vence_30d', 'Vence em 30 dias 17'], ['sem_prazo', 'Sem prazo cadastrado 5'], ['atendimentos_no_mes', 'Atendimentos no mês 23']])
    assert.match(h, /data-cartoes-garantias/, 'cartões no celular')
    const linha = texto((h.match(/<p[^>]*data-resumo-linha[^>]*>([\s\S]*?)<\/p>/) || [])[1] || '')
    assert.equal(linha, '3 garantias acionadas em 5.992 vendas com nota · 4 sem data de entrega ( 3 já entregues no Bling ) · 9 expiradas')
    assert.ok(!/data-ver-todos-pedidos|ver todos os pedidos de garantia/.test(h), 'o link "ver todos os pedidos de garantia" saiu')
    // O "SAC sem pedido": o contador no topo (só com N > 0).
    assert.equal(texto((h.match(/<button[^>]*data-sac-sem-pedido-botao[^>]*>([\s\S]*?)<\/button>/) || [])[1] || ''), 'SAC sem pedido (2)')
    // Quem registra atendimento tem o Confirmar (na tela larga e no celular).
    {
      const r = await abrir(REGISTRA_CONSULTA, { aba: 'garantia' })
      const hr = await r.h()
      const tab = (hr.match(/<table[^>]*data-tabela-garantias[\s\S]*?<\/table>/) || [])[0] || ''
      assert.deepEqual([...tab.matchAll(/<td[^>]*data-atendimento-sac[^>]*>([\s\S]*?)<\/td>/g)].map((x) => texto(x[1])), ['US-26-0031 03/11/2026 Confirmar', 'Garantia acionada abrir o caso'])
      assert.equal((hr.match(/data-confirmar-sac/g) || []).length, 2, 'uma vez na tabela, uma no cartão do celular')
    }
    assert.ok(!/seguiram|seguiu/i.test(texto(h)), 'sem o "seguiram"')
    assert.ok(!/data-vista-conversas/.test(h), 'a Garantia abre na Tabela')
    // Aberta pelo Resumo (o produto): a lista de Conversas, com o aviso e os caminhos de volta.
    t.cx.estado.abrirProduto({ nome: 'A17 pro max', chave: 'tabela:1' })
    await espera(0)
    const hc = await t.h()
    assert.match(hc, /data-vista-conversas/)
    assert.match(hc, /data-lista-conversas/)
    assert.ok(!/<th/.test(hc), 'sem a tabela na lista de Conversas')
    assert.match(texto((hc.match(/<p[^>]*data-lista-aberta[^>]*>([\s\S]*?)<\/p>/) || [])[1] || ''),
      /^Produto: A17 pro max · 2 garantias voltar para a tabela$/, 'quem não é admin não tem o "voltar para o resumo"')
    assert.ok(!/data-sac-sem-pedido/.test(hc), 'o "SAC sem pedido" é da Tabela')
    assert.ok(!/id="gar-mostrar"/.test(hc), 'o Mostrar é da Tabela')
  }
  // O admin com a lista aberta pelo Resumo tem o "voltar para o resumo".
  {
    const t = await abrir(ADMIN)
    t.cx.estado.abrirProduto({ nome: 'A17 pro max', ids: [7], coluna: 'Mensagem' })
    await espera(0)
    const hc = await t.h()
    assert.match(texto((hc.match(/<p[^>]*data-lista-aberta[^>]*>([\s\S]*?)<\/p>/) || [])[1] || ''),
      /^Produto: A17 pro max · Mensagem · 2 pedidos de garantia voltar para a tabela voltar para o resumo$/)
    assert.match(hc, /data-voltar-resumo/)
  }
  // A aba Atendimento: a lista de Conversas, cada uma com o selo do motivo e há quanto tempo (o vencido no tom de erro).
  {
    const t = await abrir(ADMIN, { aba: 'atendimento' })
    const h = await t.h()
    assert.match(h, /data-vista-conversas/)
    assert.ok(!/id="gar-busca"|id="gar-status"|data-indicadores/.test(h), 'o Atendimento não tem barra nem cartões')
    const selos = [...h.matchAll(/<span\b([^>]*data-atendimento="(\w+)"[^>]*)>([\s\S]*?)<\/span>/g)].map((x) => ({ motivo: x[2], texto: texto(x[3]), classe: classe(`<span ${x[1]}>`) }))
    assert.deepEqual(selos.map((s) => [s.motivo, s.texto]), [['no_tecnico', 'No técnico · há 32 dias · prazo vencido'], ['pedido_sem_resposta', 'Pedido de garantia sem resposta · há 1 dia']])
    assert.match(selos[0].classe, /text-destructive/, 'o vencido no tom de erro')
    assert.ok(!/destructive/.test(selos[1].classe))
    // O nome COMPLETO (sem apelido e sem cortar), o pedido do Bling e o tempo em aberto (do formulário).
    const lista = (h.match(/<ul[^>]*data-lista-conversas[\s\S]*?<\/ul>/) || [])[0] || ''
    const nomes = [...lista.matchAll(/<span([^>]*data-nome[^>]*)>([\s\S]*?)<\/span>/g)]
    assert.deepEqual(nomes.map((x) => texto(x[2])), ['Ana Maria de Albuquerque Cavalcanti Lima', 'Bruno Reis'])
    assert.ok(nomes.every((x) => !/truncate/.test(classe(`<span ${x[1]}>`))), 'o nome não corta com reticências')
    assert.deepEqual([...lista.matchAll(/<div[^>]*data-pedido-bling[^>]*>([\s\S]*?)<\/div>/g)].map((x) => texto(x[1])), ['pedido 55001 · Mercado Livre · Loja X', 'pedido 55001 · Mercado Livre · Loja X'])
    assert.deepEqual([...lista.matchAll(/<div[^>]*data-sac-da-lista[^>]*>([\s\S]*?)<\/div>/g)].map((x) => texto(x[1])),
      ['Aberto há 12 dias · SAC US-26-0032 · 23/10/2026', 'Aberto há 1 dia · SAC US-26-0033 · 03/11/2026'])
    assert.ok(!/\sdata-pedido[\s>=]/.test(lista), 'a frase da detecção pelas conversas não aparece')
    assert.match(texto((h.match(/<p[^>]*data-atendimento-explica[^>]*>([\s\S]*?)<\/p>/) || [])[1] || ''), /^Só os casos do SAC do site com a garantia acionada/)
    assert.equal(t.cx.estado.aparelhoRepetido(EM_ATENDIMENTO.itens[0]), true, 'o selo do aparelho repetiria o "No técnico"')
    assert.equal(t.cx.estado.aparelhoRepetido(EM_ATENDIMENTO.itens[1]), false)
    // Nada em atendimento (a Mala): o vazio diz isso e lembra que a garantia é da Uranyx.
    t.cx.estado.trocarDepartamento('mala')
    await t.cx.estado.carregar({ trocouFiltro: true })
    assert.equal(texto((await t.h()).match(/data-empty[^>]*>([\s\S]*?)<\/div>/)?.[1] || ''),
      'Nenhum caso em atendimento neste departamento. A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).')
  }
  // A busca que não acha garantia, mas acha o pedido: o cartão — com o "Cadastrar garantia" só para quem pode.
  {
    const t = await abrir(CADASTRA, { aba: 'garantia' })
    // (no SSR os watchers não rodam: a busca que assentou é a releitura)
    t.cx.estado.filtros.busca = BUSCA_PEDIDO
    await t.cx.estado.carregar({ trocouFiltro: true })
    assert.equal(t.corpo().busca, BUSCA_PEDIDO)
    const h = await t.h()
    assert.equal(texto((h.match(/<p[^>]*data-pedido-sem-garantia-texto[^>]*>([\s\S]*?)<\/p>/) || [])[1] || ''),
      'Pedido 288113 · Mercado Livre Loja X · nº na plataforma 2000017677253868 · compra 30/07/2026 · Entregue — ainda sem garantia: o pedido é de antes de 19/08/2026, quando o robô começou a criar a garantia pela NF.')
    assert.match(texto((h.match(/<button[^>]*data-cadastrar-garantia[^>]*>([\s\S]*?)<\/button>/) || [])[1] || ''), /^Cadastrar garantia$/)
    assert.ok(!/data-empty/.test(h), 'o cartão no lugar do vazio')
    // O botão abre o cadastro de sempre, já com o pedido.
    assert.match(sfc('../pages/garantias.vue').descriptor.template.content, /data-cadastrar-garantia @click="novaGarantia\(cartaoSemGarantia\.pedido_bling\)"/)
    t.cx.estado.novaGarantia(t.cx.estado.cartaoSemGarantia.pedido_bling)
    assert.deepEqual([t.cx.estado.formAberto, t.cx.estado.pedidoInicial], [true, '288113'])
    t.cx.estado.formAberto = false
    semGarantia = { ...PEDIDO_SEM_GARANTIA, pode_cadastrar: false }
    await t.cx.estado.carregar({ trocouFiltro: true })
    const h2 = await t.h()
    assert.match(texto(h2), /ainda sem garantia: o pedido é de antes de 19\/08\/2026/)
    assert.ok(!/data-cadastrar-garantia/.test(h2), 'sem poder cadastrar, sem o botão')
    // Sem o pedido (a busca não achou nada): o vazio diz que a busca é só do
    // departamento da pílula e oferece a mesma busca nos outros dois.
    semGarantia = null
    await t.cx.estado.carregar({ trocouFiltro: true })
    const h3 = await t.h()
    assert.ok(!/data-pedido-sem-garantia/.test(h3))
    assert.match(texto(h3), /Nenhuma garantia com esses filtros em Celular A busca procura só em Celular: se a garantia for de Mala ou Eletro, procure na pílula dela\. Ou mude a busca, o status ou o período\./)
    assert.deepEqual([...h3.matchAll(/<button\b[^>]*data-buscar-em="(\w+)"[^>]*>([\s\S]*?)<\/button>/g)].map((x) => [x[1], texto(x[2])]),
      [['mala', 'procurar em Mala'], ['eletro', 'procurar em Eletro']])
    assert.match(sfc('../pages/garantias.vue').descriptor.template.content, /:data-buscar-em="d\.chave" @click="trocarDepartamento\(d\.chave\)"/)
    const n = t.listas().length
    t.cx.estado.trocarDepartamento('eletro')
    await espera(0)
    assert.equal(t.listas().length, n + 1, 'procurar na outra pílula: uma leitura')
    assert.deepEqual(t.corpo(), { limite: 100, offset: 0, busca: BUSCA_PEDIDO, departamento: 'eletro' }, 'a mesma busca, no outro departamento')
    t.cx.estado.trocarDepartamento('celular')
    await espera(0)
    // Sem busca, o vazio não oferece as outras pílulas.
    t.cx.estado.filtros.busca = ''
    await t.cx.estado.carregar({ trocouFiltro: true })
    assert.ok(!t.cx.estado.vazio.buscaNosOutros)
    semGarantia = PEDIDO_SEM_GARANTIA
  }
  // Carregando e vazio têm estado próprio; a Mala lembra que a garantia é da Uranyx.
  {
    const p = pagina(CONSULTA, { dept: 'mala' })
    const cx = comEstado(componente('../pages/garantias.vue', p.g))
    const inicio = await html(appCom(cx.C, {}, p.g, EXTRAS))
    assert.match(inicio, /data-carregando/)
    assert.ok(!/data-nova-garantia/.test(inicio))
    await cx.estado.carregar()
    const vazio = texto((await html(appCom(cx.mesmo, {}, p.g, EXTRAS))).match(/data-empty[^>]*>([\s\S]*?)<\/div>/)?.[1] || '')
    assert.equal(vazio, 'Nenhuma garantia neste departamento ainda A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).', 'a Mala não manda cadastrar')
    // (no SSR os watchers não rodam: o "Mostrar" trocado é a releitura)
    cx.estado.mostrar = 'acionada'
    cx.estado.aplicarVista()
    await cx.estado.carregar({ trocouFiltro: true })
    assert.equal(p.corpo().mostrar, 'acionada')
    const soAcionadas = await html(appCom(cx.mesmo, {}, p.g, EXTRAS))
    assert.match(texto(soAcionadas), /Nenhuma garantia acionada ainda Em &quot;Mostrar&quot; está &quot;garantia acionada&quot;: só as vendas com o SAC do site confirmado/)
    assert.match(soAcionadas, /data-mostrar-todas/)
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
  assert.match(html, /fim = entrega \+ o prazo do produto na Tabela de Preços › Produtos · vale até o último dia, inclusive/)
  assert.ok(!/hardware|software/i.test(texto(html)), 'o cadastro sem hardware/software (06/11/2026)')
  assert.match(html, /data-fim-garantia/)

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
  // O prazo único (06/11/2026): uma barra só, "Garantia (12 meses) até …" — sem hardware/software.
  assert.equal((dados.match(/role="progressbar"/g) || []).length, 1, 'uma barra: a garantia')
  assert.match(texto(dados), /Garantia \(12 meses\) até 07\/10\/2027/)
  assert.match(texto(dados), /(\d+ dias restantes|1 dia restante|último dia hoje|terminou em 07\/10\/2027)/)
  assert.ok(!/hardware|software/i.test(texto(dados)), 'nada de hardware/software nos Detalhes')
  assert.match(texto(dados), /Produto A17 pro max/)
  assert.match(texto(dados), /Status do pedido Atendido \(Bling\)/)
  assert.match(texto(dados), /SAC do site US-26-0031 · 03\/11\/2026 — aguardando confirmar/)
  assert.match(dados, /id="garantia-detalhe-titulo"[^>]*>João Silva</, 'o nome sem o apelido no cabeçalho')
  assert.match(texto(dados), /Cadastrado por Ana/)
  assert.match(dados, /Conferir entrega agora/)
  assert.match(dados, /Corrigir dados/)
  const atend = await d1.aba('atendimentos')
  assert.match(atend, /data-linha-do-tempo/)
  assert.match(texto(atend), /Coberto/)
  assert.ok(!/hardware|software/i.test(texto(atend)), 'nem nos atendimentos registrados')
  assert.match(texto(atend), /Fim considerado na hora do vínculo: 07\/01\/2027/)
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
  assert.match(texto(h3), /Entregue — sem data no DaVinci/)
  assert.match(h3, /data-entregue-sem-data-texto/)
  assert.match(texto(h3), /início sem data de entrega/)
  assert.ok(!/O pedido ainda não tem data de entrega/.test(h3))

  // Produto sem prazo na Tabela de Preços: "Prazo não cadastrado", sem barra.
  const d4 = await abrirDetalhe(CONSULTA, { ...DET2, garantia_meses: null, fim_garantia: null, status: 'sem_prazo', status_rotulo: 'Prazo não cadastrado' })
  const h4 = await d4.aba('dados')
  assert.equal((h4.match(/role="progressbar"/g) || []).length, 0)
  assert.match(texto(h4), /Prazo não cadastrado — o produto não tem o prazo da garantia na Tabela de Preços › Produtos/)
  assert.match(h4, /data-status-garantia[^>]*>Prazo não cadastrado</)
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
  // Revisão 06/11/2026 (C8/C11): a garantia tem o prazo do produto (12 meses, até 07/10/2027) — a prévia já
  // sai sem o tipo, e o tipo não muda o fim (antes o "Hardware" dava Fora pelo fim antigo de 3 meses).
  assert.deepEqual(m.estado.previa, { cobertura: 'coberto', fim: '2027-10-07', dia: '2027-01-08' })
  m.estado.tipo = 'hardware'
  assert.equal(m.estado.previa.cobertura, 'coberto', 'o fim único, não o antigo de 3 meses (07/01/2027)')
  m.estado.tipo = 'software'
  assert.equal(m.estado.previa.fim, '2027-10-07')
  m.estado.tipo = 'hardware'
  m.estado.alternar('m1')
  assert.equal(m.estado.previa.cobertura, 'coberto', 'escolhendo a 1ª mensagem (01/12) fica Coberto')
  // Sem o prazo do produto: a prévia espera o tipo e usa o fim antigo dele (o que o servidor grava).
  const SEM_PRAZO = { ...LINHA, garantia_meses: null, fim_garantia: null, status: 'sem_prazo', status_rotulo: 'Prazo não cadastrado' }
  const ms = montar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [SEM_PRAZO] } }, { user: REGISTRA, api: fakeApi })
  assert.equal(ms.estado.escolhida.id, 7)
  assert.equal(ms.estado.previa, null, 'sem prazo e sem tipo: nada a prever')
  ms.estado.tipo = 'hardware'
  assert.deepEqual(ms.estado.previa, { cobertura: 'fora_da_garantia', fim: '2027-01-07', dia: '2027-01-08' })
  // Na tela: "Tipo do problema" com os rótulos novos e a prévia com o fim único — sem hardware/software.
  const extrasModal = { GarantiaStatus: '../components/GarantiaStatus.vue', GarantiaCobertura: '../components/GarantiaCobertura.vue' }
  const hModal = texto(await renderizar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: SITUACAO }, { user: REGISTRA, api: fakeApi }, extrasModal))
  assert.match(hModal, /2\. Tipo do problema Defeito no aparelho parte física: .* Sistema \/ aplicativo sistema, app/)
  assert.match(hModal, /Coberto atendimento em 08\/01\/2027 · garantia até 07\/10\/2027\./)
  assert.ok(!/hardware|software|\bHW\b|\bSW\b/i.test(hModal), 'o modal sem hardware/software')
  const hSemPrazo = texto(await renderizar('../components/GarantiaVincular.vue', { conversa: CONVERSA, mensagens: MSGS, situacao: { ...SITUACAO, garantias: [SEM_PRAZO] } }, { user: REGISTRA, api: fakeApi }, extrasModal))
  assert.match(hSemPrazo, /O produto não tem prazo na Tabela de Preços › Produtos: escolha o tipo do problema/)
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
  assert.equal(m2.estado.erros.tipo_problema, 'Escolha o tipo do problema.')

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
  assert.match(texto(sem), /Ativa/)
  assert.ok(!/data-outro-pedido/.test(sem), 'a garantia é do pedido da conversa')
  // Revisão 06/11/2026 (C10): o fim ÚNICO do painel ao lado do selo — sem os dois prazos antigos — e o
  // tipo do atendimento vinculado com o rótulo do modal.
  assert.match(texto(sem), /garantia até 07\/10\/27 · 2 atend\./)
  assert.match(texto(sem), /· Defeito no aparelho · Coberto/)
  assert.ok(!/HW até|SW até|hardware|software/i.test(texto(sem)), 'sem hardware/software no bloco')
  const semPrazo = await renderizar('../components/AtendimentoGarantia.vue', { situacao: { ...SIT, alerta_cpf_sem_garantia: false, garantias: [{ ...LINHA, status: 'sem_prazo', status_rotulo: 'Prazo não cadastrado', garantia_meses: null, fim_garantia: null }] }, podeVincular: false, podeAbrir: true, podeCadastrar: false }, {}, extras)
  assert.match(texto(semPrazo), /Prazo não cadastrado #7 · NF 10234 · série 1/, 'o status novo do painel no selo')
  assert.match(texto(semPrazo), /prazo não cadastrado · 2 atend\./)
  const semEntrega = await renderizar('../components/AtendimentoGarantia.vue', { situacao: { ...SIT, alerta_cpf_sem_garantia: false, garantias: [{ ...LINHA, status: 'aguardando_entrega', status_rotulo: 'Aguardando entrega', data_inicio: null, fim_garantia: null }] }, podeVincular: false, podeAbrir: true, podeCadastrar: false }, {}, extras)
  assert.match(texto(semEntrega), /Aguardando entrega[\s\S]*garantia até — · 2 atend\./)
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
  // O aviso depois de vincular diz o tipo com o rótulo do modal (06/11/2026).
  {
    const aviso = setup.slice(setup.indexOf('function aoVincularGarantia'), setup.indexOf('void carregarGarantia(props.conversaId)', setup.indexOf('function aoVincularGarantia')))
    assert.match(aviso, /rotuloDoTipo\(a\.tipo_problema\)/)
    assert.ok(!/'Hardware'|'Software'/.test(aviso), 'o aviso sem Hardware/Software')
  }
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

// ═══════════════════════════════════════════════════════════════ garantia por produto (aba Resumo)
async function testarResumo() {
  // 04/11/2026 (as 3 partes): a aba Resumo, só do admin — Vendas (Vendidas,
  // Saldo e a % = gasto ÷ saldo × 100), Atendimento (como pediu) e Manutenção
  // (só o Gasto da tela Reembolso desde 05/11/2026: "em manutenção deixe
  // somente o gasto"). A pílula fica na página.
  const produto = {
    chave: 'tabela:1', departamento: 'celular', nome: 'A17 pro max', skus: ['dg052', 'dg053'], na_tabela: true,
    vendidas: 100, pediram: 6, seguiram: 2,
    origens: { mensagem: 4, sac: 0, devolucao: 2 },
    consertos: 1, perdidos: 0,
    gasto: 229.4, gasto_conserto: 200, gasto_perdidos: 0, gasto_cliente: 29.4, saldo: 6650,
    pedidos: [1, 2, 3, 4, 5, 6],
    pedidos_por_coluna: { mensagem: [1, 2, 3, 4], devolucao: [5, 6], consertos: [6], gasto: [5, 6] },
  }
  const outro = {
    chave: 'tabela:2', departamento: 'celular', nome: 'DG019', skus: ['dg019'], na_tabela: true,
    vendidas: 300, pediram: 1, seguiram: 0, origens: { mensagem: 1 }, consertos: 0, perdidos: 1,
    gasto: 120.6, gasto_conserto: 0, gasto_perdidos: 120.6, gasto_cliente: 0, saldo: 15000.4,
    pedidos: [7], pedidos_por_coluna: { mensagem: [7], perdidos: [7], gasto: [7] },
  }
  const semSaldo = {
    chave: 'tabela:3', departamento: 'eletro', nome: 'U9', skus: ['u9'], na_tabela: true, vendidas: 3, pediram: 0, seguiram: 0,
    origens: {}, consertos: 0, perdidos: 0, gasto: 0, gasto_conserto: 0, gasto_perdidos: 0, gasto_cliente: 0, saldo: 0, pedidos: [], pedidos_por_coluna: {},
  }
  // Vendido fora da Tabela de Preços (05/11/2026): não aparece no Resumo nem no rodapé.
  const fora = {
    chave: 'sku:dg1028', departamento: 'celular', nome: 'Uranyx C57S AVULSO', skus: ['dg1028'], na_tabela: false,
    vendidas: 50, pediram: 2, seguiram: 0, origens: { mensagem: 2 }, consertos: 0, perdidos: 0,
    gasto: 40, gasto_conserto: 0, gasto_perdidos: 0, gasto_cliente: 40, saldo: 20000, pedidos: [8, 9], pedidos_por_coluna: { mensagem: [8, 9], gasto: [9] },
  }
  const RESUMO = {
    vendidas: 403, pediram: 7, seguiram: 2, hoje: '2026-11-04', ve_valores: true,
    departamentos: [
      { chave: 'celular', nome: 'Celular', vendidas: 400, pediram: 7, saldo: 21650.4, gasto: 350 },
      { chave: 'mala', nome: 'Mala', vendidas: 0, pediram: 0, saldo: 0, gasto: 0 },
      { chave: 'eletro', nome: 'Eletro', vendidas: 3, pediram: 0, saldo: 0, gasto: 0 },
    ],
    produtos: [outro, produto, semSaldo, fora],
  }
  const montarResumo = async (resumo, departamento = 'celular', filtros = {}) => {
    const emitidos = []
    const todos = []
    const g = { user: ADMIN, api: async (url) => { assert.equal(url, '/api/garantias/resumo'); return resumo } }
    const cx = comEstado(componente('../components/GarantiaResumo.vue', g))
    const props = { filtros: { ...L.FILTROS_PADRAO, ...filtros }, departamento, onAbrir: (x) => emitidos.push(x), onTodos: () => todos.push(1) }
    await html(appCom(cx.C, props, g))
    await new Promise((r) => setTimeout(r, 0))
    return { cx, emitidos, todos, h: await html(appCom(cx.mesmo, props, g)) }
  }
  const { cx, emitidos, todos, h } = await montarResumo(RESUMO)
  const col = (k) => cx.estado.colunas.find((c) => c.chave === k)
  const celula = (html, chave, k) => {
    const linha = (html.match(new RegExp(`<tr[^>]*data-produto="${chave}"[^>]*>([\\s\\S]*?)</tr>`)) || [])[1] || ''
    const td = new RegExp(`<td([^>]*data-celula="${k}"[^>]*)>([\\s\\S]*?)</td>`).exec(linha)
    return td ? { texto: texto(td[2]), titulo: (/ title="([^"]*)"/.exec(td[1]) || [])[1] } : null
  }
  const total = (html, k) => texto((html.match(new RegExp(`<td[^>]*data-total="${k}"[^>]*>([\\s\\S]*?)</td>`)) || [])[1] || '')
  // As colunas, na ordem da SPEC — e as que saíram não voltam.
  const titulos = [...h.matchAll(/<th[^>]*data-coluna="\w+"[^>]*>([\s\S]*?)<\/th>/g)].map((x) => texto(x[1]))
  assert.deepEqual(titulos, ['SKU', 'Produto', 'Vendidas', 'Saldo', '%', 'Mensagem', 'SAC', 'Devolução', 'Gasto R$'])
  const grupos = [...h.matchAll(/<th([^>]*data-grupo="(\w+)"[^>]*)>([\s\S]*?)<\/th>/g)].map((x) => [x[2], texto(x[3]), Number((/colspan="(\d+)"/.exec(x[1]) || [])[1])])
  assert.deepEqual(grupos, [['venda', 'Vendas', 3], ['atendimento', 'Atendimento', 3], ['manutencao', 'Manutenção', 1]])
  for (const saiu of ['Pediram', 'Seguiram', 'Outros', 'Ativa', 'Só software', 'Aguardando', 'Expirada', 'Atendidos', 'No técnico', 'Consertos', 'Perdidos', 'Como pediu', 'Situação de quem pediu', 'Manutenção e perdas']) {
    assert.ok(!titulos.includes(saiu) && !grupos.some((g) => g[1] === saiu), `sem "${saiu}"`)
  }
  assert.equal((/<th[^>]*title="([^"]*)"[^>]*data-coluna="pct"/.exec(h) || [])[1], 'Gasto ÷ Saldo × 100')
  assert.ok(!/data-departamento=/.test(h), 'as pílulas ficam na página')
  // A largura mínima é a conta das colunas na tela (112 + 186 + 7 × 77).
  assert.match(h, /<table[^>]*style="min-width:837px;?"/)
  // Só o departamento da pílula; Vendidas (maior em cima) é a ordem padrão.
  assert.deepEqual([...h.matchAll(/<tr[^>]*data-produto="([^"]+)"/g)].map((x) => x[1]), ['tabela:2', 'tabela:1'])
  assert.ok(!/fora da Tabela de Preços|Uranyx C57S AVULSO/.test(h), 'o vendido fora da Tabela de Preços não aparece')
  assert.match(h, /aria-sort="descending"[^>]*data-coluna="vendidas"/)
  // Os números: o Saldo e o Gasto em reais sem os centavos (o exato no mouse); a % com 2 casas.
  assert.deepEqual(celula(h, 'tabela:1', 'saldo'), { texto: '6.650', titulo: 'R$ 6.650,00' })
  assert.deepEqual(celula(h, 'tabela:1', 'pct'), { texto: '3,45%', titulo: 'gasto R$ 229,40 ÷ saldo R$ 6.650,00 × 100' })
  assert.equal(celula(h, 'tabela:2', 'pct').texto, '0,80%')
  assert.equal(celula(h, 'tabela:2', 'saldo').texto, '15.000')
  assert.deepEqual(celula(h, 'tabela:1', 'gasto'), {
    texto: '229',
    titulo: 'ver os 2 pedidos de garantia — Gasto R$ · R$ 229,40: conserto R$ 200,00 + perdidos R$ 0,00 + reembolso ao cliente R$ 29,40 — da tela Reembolso',
  })
  assert.deepEqual(['vendidas', 'mensagem', 'sac', 'devolucao'].map((k) => celula(h, 'tabela:1', k).texto), ['100', '4', '0', '2'])
  for (const k of ['consertos', 'perdidos']) assert.equal(celula(h, 'tabela:1', k), null, `sem a célula ${k}`)
  assert.equal(celula(h, 'tabela:1', 'sac').titulo, 'nenhum pedido nesta coluna', 'o zero não herda o "ver as N garantias" da linha')
  assert.equal(celula(h, 'tabela:1', 'mensagem').titulo, 'ver os 4 pedidos de garantia — Mensagem')
  // O rodapé: as somas; a % = Σgasto ÷ Σsaldo × 100.
  assert.deepEqual(['vendidas', 'saldo', 'pct', 'mensagem', 'sac', 'devolucao', 'gasto'].map((k) => total(h, k)),
    ['400', '21.650', '1,62%', '5', '0', '2', '350'])
  assert.ok(!/data-total="(consertos|perdidos)"/.test(h), 'sem Consertos e Perdidos no rodapé')
  assert.match(h, /title="R\$ 350,00: conserto R\$ 200,00 \+ perdidos R\$ 120,60 \+ reembolso ao cliente R\$ 29,40 — da tela Reembolso"/, 'o Gasto do rodapé aberto no mouse')
  assert.match(texto((h.match(/<tr[^>]*data-total-departamento[^>]*>([\s\S]*?)<\/tr>/) || [])[1] || ''), /^Total — Celular /)
  // A conta da %, discreta; a busca por SKU ou produto continua.
  assert.equal(texto((h.match(/<span[^>]*data-calculo[^>]*>([\s\S]*?)<\/span>/) || [])[1] || ''), '% = gasto ÷ saldo × 100')
  assert.match(h, /placeholder="buscar SKU ou produto…"/)
  // Os cliques: a linha (e Vendidas, Saldo, %) abre todas as garantias do produto (a chave); o
  // número das outras colunas, só aqueles pedidos (os ids); o zero não abre nada.
  const clique = (p, k) => { let parou = false; cx.estado.clicarCelula({ stopPropagation: () => { parou = true } }, p, col(k)); return parou }
  cx.estado.abrir(produto)
  assert.deepEqual(emitidos.at(-1), { nome: 'A17 pro max', chave: 'tabela:1' })
  let antes = emitidos.length
  for (const k of ['vendidas', 'saldo', 'pct']) assert.equal(clique(produto, k), false, `${k}: o clique é o da linha`)
  assert.equal(emitidos.length, antes)
  assert.equal(clique(produto, 'devolucao'), true)
  assert.deepEqual(emitidos.at(-1), { nome: 'A17 pro max', ids: [5, 6], coluna: 'Devolução' })
  clique(produto, 'gasto')
  assert.deepEqual(emitidos.at(-1), { nome: 'A17 pro max', ids: [5, 6], coluna: 'Gasto R$' })
  clique(outro, 'gasto')
  assert.deepEqual(emitidos.at(-1), { nome: 'DG019', ids: [7], coluna: 'Gasto R$' })
  assert.equal(col('consertos'), undefined)
  assert.equal(col('perdidos'), undefined)
  antes = emitidos.length
  assert.equal(clique(produto, 'sac'), true, 'o zero não deixa o clique virar o da linha')
  assert.equal(emitidos.length, antes, 'o zero não abre nada')
  // A linha sem venda nenhuma não abre; o "Ver todos os pedidos de garantia" saiu (06/11/2026).
  cx.estado.abrir({ ...produto, vendidas: 0 })
  assert.equal(emitidos.length, antes)
  assert.equal(cx.estado.dicaDaLinha(produto), 'ver as 100 garantias de A17 pro max')
  assert.ok(!/data-ver-todos|Ver todos os pedidos de garantia/.test(h))
  assert.ok(!/'todos'/.test(sfc('../components/GarantiaResumo.vue').descriptor.scriptSetup.content), 'nem o emit "todos"')
  // Ordenar pela %: o maior gasto sobre o saldo em cima.
  cx.estado.ordenar('pct')
  assert.deepEqual(cx.estado.linhas.map((p) => p.chave), ['tabela:1', 'tabela:2'])
  // O Eletro (sem saldo): a % é "—", na linha e no rodapé.
  const eletro = await montarResumo(RESUMO, 'eletro')
  assert.equal(celula(eletro.h, 'tabela:3', 'pct').texto, '—')
  assert.equal(total(eletro.h, 'pct'), '—')
  assert.match(texto((eletro.h.match(/<tr[^>]*data-total-departamento[^>]*>([\s\S]*?)<\/tr>/) || [])[1] || ''), /^Total — Eletro /)
  // O período pela data da venda (05/11/2026): o título diz de quais vendas são os números.
  assert.ok(!/data-periodo-vendas/.test(h), 'sem datas, sem o período no título')
  const comDatas = await montarResumo(RESUMO, 'celular', { periodo: 'venda', de: '2026-11-01', ate: '2026-11-05' })
  assert.equal(texto((comDatas.h.match(/<span[^>]*data-periodo-vendas[^>]*>([\s\S]*?)<\/span>/) || [])[1] || ''), '· vendas de 01/11/2026 a 05/11/2026')
  const soAte = await montarResumo(RESUMO, 'celular', { periodo: 'venda', ate: '2026-11-05' })
  assert.match(soAte.h, /vendas até 05\/11\/2026/)
  const peloPedido = await montarResumo(RESUMO, 'celular', { periodo: 'pedido', de: '2026-11-01', ate: '2026-11-05' })
  assert.ok(!/data-periodo-vendas/.test(peloPedido.h), 'pela data do pedido, o título não fala de vendas')
  // A Mala, sem venda: o vazio lembra que a garantia é da Uranyx.
  const mala = await montarResumo(RESUMO, 'mala')
  assert.equal(texto((mala.h.match(/<td[^>]*data-sem-produtos[^>]*>([\s\S]*?)<\/td>/) || [])[1] || ''),
    'Nenhuma venda neste departamento com esses filtros. A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).')
  // Sem os reais (o servidor não mandou): sem Saldo, % e Gasto — nenhum real na tela.
  const semValores = { ...produto, gasto: null, gasto_conserto: null, gasto_perdidos: null, gasto_cliente: null, saldo: null, pedidos_por_coluna: { ...produto.pedidos_por_coluna, gasto: undefined } }
  const r2 = await montarResumo({ ...RESUMO, ve_valores: false, produtos: [semValores] })
  assert.deepEqual(r2.cx.estado.colunas.map((c) => c.rotulo), ['Vendidas', 'Mensagem', 'SAC', 'Devolução'])
  assert.match(r2.h, /<table[^>]*style="min-width:606px;?"/, 'a largura acompanha as colunas')
  // Sem o Gasto a Manutenção fica sem coluna: o título do grupo some junto.
  assert.deepEqual([...r2.h.matchAll(/data-grupo="(\w+)"/g)].map((x) => x[1]), ['venda', 'atendimento'])
  assert.ok(!/R\$/.test(texto(r2.h)), 'nenhum real na tela de quem não vê os valores')
  // O 403 de quem não é admin vira frase (a página nem chama, mas se chamar).
  const erro403 = comEstado(componente('../components/GarantiaResumo.vue', { user: CONSULTA, api: async () => { throw Object.assign(new Error('403'), { statusCode: 403, data: { detail: { code: 'so_admin' } } }) } }))
  await html(appCom(erro403.C, { filtros: { ...L.FILTROS_PADRAO }, departamento: 'celular' }, {}))
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(erro403.estado.erro, L.ERROS.so_admin)
  assert.equal(L.reaisBR(1234.5), 'R$ 1.234,50')
  void todos

  // O selo do aparelho: o vencido (o servidor diz) na cor do perdido; corta
  // com "…" numa coluna estreita (o texto inteiro no mouse); sem texto, nada.
  const selo = async (situacao, rotulo, vencido) => html(appCom(componente('../components/GarantiaAparelho.vue', {}), { situacao, rotulo, vencido }))
  const tecnico = await selo('no_tecnico', 'No técnico · faltam 5 dias', false)
  assert.match(tecnico, /data-aparelho="no_tecnico"[^>]*>No técnico · faltam 5 dias</)
  assert.match(tecnico, /bg-orange-50/)
  assert.match(tecnico, /max-w-full truncate/)
  assert.match(tecnico, /title="No técnico · faltam 5 dias — o aparelho desta garantia na tela Devoluções"/)
  assert.match(await selo('no_tecnico', 'No técnico · vencido há 3 dias', true), /bg-rose-50/)
  assert.match(await selo('consertado', 'Consertado (Novo)', false), /bg-teal-50/)
  assert.match(await selo('devolvido', 'Devolvido (Novo)', false), /bg-slate-50/)
  const aCaminho = await selo('a_caminho', 'Devolução a caminho', false)
  assert.match(aCaminho, /bg-indigo-50/)
  assert.match(aCaminho, /title="Devolução a caminho — devolução aceita na plataforma, o aparelho está voltando/, 'não manda procurar na tela Devoluções')
  const reembolso = await selo('reembolso', 'Reembolso ao cliente', false)
  assert.match(reembolso, /bg-yellow-50/)
  assert.match(reembolso, /title="Reembolso ao cliente — reembolso ao cliente \(tela Reembolso ou pago pela plataforma\)"/)
  assert.equal(texto(await selo(null, null, false)), '')
}

// ═══════════════════════════════════════════════════════════════ SAC do site (06/11/2026)
const SAC_DETALHE = {
  id: 32, protocolo: 'US-26-0032', recebido_em: '2026-10-23T12:00:00Z', conversa_id: 'conv-sac-32',
  confirmado_em: '2026-10-24T14:00:00Z', confirmado_por: { id: 'u', nome: 'Ana' }, encerrado_em: null, encerrado_por: null, encerrado_motivo: null,
  secoes: [
    { titulo: 'Atendimento', campos: [{ rotulo: 'Protocolo', valor: 'US-26-0032' }, { rotulo: 'Data de envio', valor: '23/10/2026 09:00' }] },
    { titulo: 'Comprador', campos: [{ rotulo: 'Nome', valor: 'Ana Maria' }, { rotulo: 'CPF', valor: '***.982.247-**' }, { rotulo: 'E-mail', valor: null }] },
    { titulo: 'Defeito', campos: [{ rotulo: 'Descrição', valor: 'A tela apaga.\nE volta.' }] },
  ],
  anexos: [
    { id: 'a1', nome: 'produto.jpg', tipo: 'image/jpeg', tamanho: 2048, papel: 'Foto do produto', url: '/api/garantias/sac/32/anexos/a1' },
    { id: 'a2', nome: 'video.mp4', tipo: 'video/mp4', tamanho: 4_000_000, papel: 'Vídeo', url: '/api/garantias/sac/32/anexos/a2' },
    { id: 'a3', nome: 'nota.pdf', tipo: 'application/pdf', tamanho: 50_000, papel: null, url: '/api/garantias/sac/32/anexos/a3' },
  ],
}
async function testarSac() {
  // O Resumo do SAC: as seções e a Documentação; as fotos entram como blob (rota autenticada) e o
  // endereço local é devolvido ao sair.
  {
    const chamadas = []
    const desmontar = []
    const g = { user: REGISTRA_CONSULTA, api: async (url, opts = {}) => { chamadas.push({ url, opts }); return new Blob(['x'], { type: 'image/jpeg' }) }, onBeforeUnmount: (fn) => desmontar.push(fn) }
    const cx = comEstado(componente('../components/GarantiaSacResumo.vue', g))
    await html(appCom(cx.C, { sac: SAC_DETALHE }, g))
    await new Promise((r) => setTimeout(r, 0))
    assert.deepEqual(chamadas.map((c) => [c.url, c.opts.responseType]), [['/api/garantias/sac/32/anexos/a1', 'blob']], 'só a foto vem ao abrir (vídeo e PDF no clique)')
    const h = await html(appCom(cx.mesmo, { sac: SAC_DETALHE }, g))
    assert.deepEqual([...h.matchAll(/data-sac-secao="([^"]+)"/g)].map((x) => x[1]), ['Atendimento', 'Comprador', 'Defeito'])
    assert.match(texto(h), /Resumo do SAC protocolo US-26-0032/)
    assert.match(texto(h), /CPF \*\*\*\.982\.247-\*\* E-mail —/, 'o CPF como o servidor manda (mascarado); vazio = —')
    assert.match(h, /<img src="blob:[^"]+" alt="Foto do produto"/, 'a foto pelo endereço local')
    assert.match(texto(h), /Vídeo video\.mp4/)
    assert.match(texto(h), /nota\.pdf/)
    await cx.estado.abrirVideo(SAC_DETALHE.anexos[1])
    assert.equal(chamadas.at(-1).url, '/api/garantias/sac/32/anexos/a2')
    assert.match(await html(appCom(cx.mesmo, { sac: SAC_DETALHE }, g)), /<video[^>]*src="blob:[^"]*"[^>]*controls/, 'o vídeo toca no painel')
    const locais = Object.values(cx.estado.locais)
    assert.equal(locais.length, 2)
    for (const f of desmontar) f()
    assert.deepEqual(cx.estado.locais, {}, 'ao sair, os endereços locais são devolvidos')
    assert.ok(!/hardware|software/i.test(texto(h)))
  }
  // Revisão 06/11/2026: saiu da tela com a foto chegando — o arquivo que chega depois NÃO vira endereço local
  // (ninguém mais o devolveria ao navegador), nem dá aviso de erro.
  {
    const criados = []
    const original = URL.createObjectURL
    URL.createObjectURL = (b) => { criados.push(b); return `blob:teste-${criados.length}` }
    try {
      const desmontar = []
      const pendentes = []
      const avisos = []
      const g = {
        user: REGISTRA_CONSULTA,
        api: () => new Promise((ok, falha) => pendentes.push({ ok, falha })),
        onBeforeUnmount: (fn) => desmontar.push(fn),
        toasts: { success() {}, info() {}, warning() {}, error: (t) => avisos.push(t) },
      }
      const m = montar('../components/GarantiaSacResumo.vue', { sac: SAC_DETALHE }, g)
      assert.equal(pendentes.length, 1, 'a foto começou a baixar')
      assert.ok(m.estado.baixando.has('a1'))
      const video = m.estado.abrirVideo(SAC_DETALHE.anexos[1])
      assert.equal(pendentes.length, 2)
      for (const f of desmontar) f()
      pendentes[0].ok(new Blob(['x'], { type: 'image/jpeg' }))
      pendentes[1].falha(Object.assign(new Error('500'), { statusCode: 500 }))
      await video
      await new Promise((r) => setTimeout(r, 0))
      assert.deepEqual(criados, [], 'nenhum endereço local criado depois de sair')
      assert.deepEqual(m.estado.locais, {})
      assert.deepEqual(avisos, [], 'nem o aviso do que falhou depois de sair')
      // Ainda na tela, o mesmo arquivo vira endereço local (o caso normal).
      const g2 = { ...g, onBeforeUnmount: () => {} }
      pendentes.length = 0
      const m2 = montar('../components/GarantiaSacResumo.vue', { sac: SAC_DETALHE }, g2)
      pendentes[0].ok(new Blob(['x'], { type: 'image/jpeg' }))
      await new Promise((r) => setTimeout(r, 0))
      assert.deepEqual(Object.keys(m2.estado.locais), ['a1'])
      assert.equal(criados.length, 1)
    } finally {
      URL.createObjectURL = original
    }
  }
  // "SAC sem pedido": a lista (sem CPF), a busca manual (POST /busca, o termo no corpo) e o vínculo.
  {
    const chamadas = []
    const LISTA_SAC = { itens: [{ id: 41, protocolo: 'US-26-0041', recebido_em: '2026-11-02T12:00:00Z', nome: 'Carla Dias', pedido_citado: '2000099', nota_fiscal: null, modelo: 'A17', local_compra: 'Mercado Livre', loja_vendedor: 'Uranyx' }], total: 1 }
    const api = async (url, opts = {}) => {
      chamadas.push({ url, opts })
      if (url === '/api/garantias/sac/sem-pedido') return LISTA_SAC
      if (url === '/api/garantias/busca') return [LINHA]
      if (url === '/api/garantias/sac/41/vincular') return { ...SAC_RECEBIDO, id: 41 }
      throw new Error(url)
    }
    const m = montar('../components/GarantiaSacSemPedido.vue', {}, { user: REGISTRA_CONSULTA, api })
    await m.montar()
    assert.equal(chamadas[0].url, '/api/garantias/sac/sem-pedido')
    assert.equal(m.estado.total, 1)
    m.estado.abrirBusca(LISTA_SAC.itens[0])
    await new Promise((r) => setTimeout(r, 0))
    assert.deepEqual(chamadas.at(-1), { url: '/api/garantias/busca', opts: { method: 'POST', body: { q: '2000099' } } }, 'a busca já vem com o pedido citado, no corpo')
    const antes = globalThis.window
    try {
      globalThis.window = { confirm: () => true }
      await m.estado.vincular(LISTA_SAC.itens[0], LINHA)
    } finally {
      globalThis.window = antes
    }
    assert.deepEqual(chamadas.at(-1), { url: '/api/garantias/sac/41/vincular', opts: { method: 'POST', body: { garantia_id: 7 } } })
    assert.deepEqual(m.emitidos.at(-1), ['vinculado', 41])
    assert.equal(m.estado.itens.length, 0, 'o ligado sai da lista')
    // Revisão 06/11/2026 (C6): outra pessoa escreveu na conversa do SAC — o servidor recusa (409
    // sac_outra_pessoa) e a tela diz por quê; o caso fica na lista.
    {
      const avisos = []
      const OUTRO = { ...LISTA_SAC.itens[0], id: 42, protocolo: 'US-26-0042' }
      const api2 = async (url, opts = {}) => {
        if (url === '/api/garantias/sac/sem-pedido') return { itens: [OUTRO], total: 1 }
        if (url === '/api/garantias/sac/42/vincular') throw Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'sac_outra_pessoa' } } })
        return api(url, opts)
      }
      const m2 = montar('../components/GarantiaSacSemPedido.vue', {}, { user: REGISTRA_CONSULTA, api: api2, toasts: { success() {}, info() {}, warning() {}, error: (t, d) => avisos.push([t, d]) } })
      await m2.montar()
      const antes2 = globalThis.window
      try {
        globalThis.window = { confirm: () => true }
        await m2.estado.vincular(OUTRO, LINHA)
      } finally {
        globalThis.window = antes2
      }
      assert.deepEqual(avisos, [['Não deu para ligar o SAC', L.ERROS.sac_outra_pessoa]])
      assert.deepEqual(m2.estado.itens.map((x) => x.id), [42], 'o caso fica em SAC sem pedido')
      assert.equal(m2.emitidos.length, 0)
    }
    const gs = { user: REGISTRA_CONSULTA, api, onMounted: (fn) => fn() }
    const cs = comEstado(componente('../components/GarantiaSacSemPedido.vue', gs))
    await html(appCom(cs.C, {}, gs))
    await new Promise((r) => setTimeout(r, 0))
    const h = await html(appCom(cs.mesmo, {}, gs))
    assert.match(texto(h), /US-26-0041 02\/11\/2026 Carla Dias procurar a venda Pedido citado 2000099 Nota fiscal — Modelo A17 Local da compra Mercado Livre · Uranyx/)
    assert.ok(!/\d{3}\.\d{3}\.\d{3}-\d{2}|\*\*\*\./.test(h) && !camposTs_SacSemPedido.includes('cpf'), 'sem CPF')
  }
  // O caso no cabeçalho da conversa (aba Atendimento): nome inteiro, pedido do Bling, tempo em aberto,
  // Encerrar, o Resumo do SAC no topo e a resposta só por e-mail (pela conversa do protocolo).
  {
    const chamadas = []
    const HIST = { garantia_id: 9, restrito: false, desde: null, cortadas: 0, conversas: [{ id: 'conv-sac-32', plataforma: 'site', canal: 'email', protocolo: 'US-26-0032', link: '/atendimento?conversa=conv-sac-32', email: true, como: 'pedido' }, { id: 'c-ml', plataforma: 'ml', canal: 'chat', link: '/atendimento?conversa=c-ml', email: false, como: 'pedido' }],
      mensagens: [{ id: 'mx', conversa_id: 'c-ml', autor: 'cliente', origem: 'externo', tipo: 'texto', texto: 'oi', anexos: [], enviada_em: '2026-10-25T12:00:00Z', status: null }], marcos: [] }
    const api = async (url, opts = {}) => {
      chamadas.push({ url, opts })
      if (url === '/api/garantias/9/historico') return HIST
      if (url === '/api/garantias/9/sac') return SAC_DETALHE
      if (url === '/api/garantias/sac/32/encerrar') return { ...SAC_ACIONADO, encerrado_em: '2026-11-04T16:00:00Z', encerrado_motivo: 'pessoa' }
      if (url === '/api/atendimento/conversas/conv-sac-32/responder') return { mensagem: { id: 'nova' } }
      throw new Error(url)
    }
    const CASO = { ...LINHA, id: 9, cliente_nome: 'Ana Maria de Albuquerque Cavalcanti Lima (aninha_cav)', nome: 'Ana Maria de Albuquerque Cavalcanti Lima', sac: SAC_ACIONADO }
    const g = { user: REGISTRA_CONSULTA, api }
    const cx = comEstado(componente('../components/GarantiaHistorico.vue', g))
    const mudou = []
    const props = { garantia: CASO, versao: 0, hoje: '2026-11-04', onMudou: (...a) => mudou.push(a) }
    await html(appCom(cx.C, props, g))
    await new Promise((r) => setTimeout(r, 0))
    const h = await html(appCom(cx.mesmo, props, g))
    assert.match(h, /<h2[^>]*data-nome-caso[^>]*>Ana Maria de Albuquerque Cavalcanti Lima<\/h2>/)
    assert.ok(!/truncate/.test(h.match(/<h2[^>]*data-nome-caso[^>]*>/)[0]), 'o nome não corta')
    assert.match(texto(h), /pedido 55001 \(2000012345\) · Mercado Livre · Loja X · Atendido/)
    assert.match(texto((h.match(/<p[^>]*data-caso-sac[^>]*>([\s\S]*?)<\/p>/) || [])[1] || ''), /^Aberto há 12 dias · SAC US-26-0032 · enviado em 23\/10\/2026 · confirmado .* por Ana$/)
    assert.match(h, /data-encerrar-atendimento/)
    assert.match(texto(h), /Fim da garantia 07\/10\/27 \(12 meses\)/)
    assert.ok(!/Fim HW|Fim SW|hardware|software/i.test(texto(h)), 'sem hardware/software no cabeçalho')
    assert.match(h, /data-resposta-email/, 'a resposta por e-mail no fim da conversa')
    assert.equal(cx.estado.conversaDoSac, 'conv-sac-32', 'só pela conversa do SAC')
    // Enviar: só com a prévia liberada (a trava '' = pode).
    cx.estado.resposta = 'Olá, recebemos.'
    await cx.estado.responder()
    assert.ok(!chamadas.some((c) => c.url.endsWith('/responder')), 'sem a prévia, não envia')
    cx.estado.travaEmail = ''
    await cx.estado.responder()
    assert.deepEqual(chamadas.filter((c) => c.url.endsWith('/responder')), [{ url: '/api/atendimento/conversas/conv-sac-32/responder', opts: { method: 'POST', body: { texto: 'Olá, recebemos.' } } }])
    assert.equal(chamadas.at(-1).url, '/api/garantias/9/historico', 'a conversa relê (o balão da resposta na fila)')
    assert.equal(cx.estado.resposta, '')
    // Encerrar: pergunta antes; desistiu, nada sai; confirmou, a página relê.
    const antes = globalThis.window
    try {
      let resp = false
      globalThis.window = { confirm: () => resp }
      await cx.estado.encerrar()
      assert.ok(!chamadas.some((c) => c.url.endsWith('/encerrar')))
      resp = true
      await cx.estado.encerrar()
    } finally {
      globalThis.window = antes
    }
    assert.deepEqual(chamadas.at(-1), { url: '/api/garantias/sac/32/encerrar', opts: { method: 'POST' } })
    // O caso encerrado vai para a página no "mudou" (ela põe na linha aberta); o histórico não é relido aqui.
    assert.deepEqual(mudou, [[{ ...SAC_ACIONADO, encerrado_em: '2026-11-04T16:00:00Z', encerrado_motivo: 'pessoa' }]])
    await new Promise((r) => setTimeout(r, 0))
    assert.equal(chamadas.at(-1).url, '/api/garantias/sac/32/encerrar', 'sem nova leitura do /historico (grava "consultou")')
    // Caso encerrado: sem o Encerrar e sem a caixa de resposta; o tempo para de contar.
    const ENC = { ...CASO, sac: { ...SAC_ACIONADO, encerrado_em: '2026-10-30T12:00:00Z', encerrado_motivo: 'terminou' } }
    const cx2 = comEstado(componente('../components/GarantiaHistorico.vue', g))
    await html(appCom(cx2.C, { ...props, garantia: ENC }, g))
    await new Promise((r) => setTimeout(r, 0))
    const h2 = await html(appCom(cx2.mesmo, { ...props, garantia: ENC }, g))
    assert.ok(!/data-encerrar-atendimento|data-resposta-email/.test(h2))
    assert.match(texto(h2), /Ficou aberto 7 dias/)
    assert.match(texto(h2), /\(terminou sozinho\)/)
    // Sem caso do SAC: não lê o /sac.
    const n = chamadas.length
    const cx3 = comEstado(componente('../components/GarantiaHistorico.vue', g))
    await html(appCom(cx3.C, { ...props, garantia: { ...CASO, id: 9, sac: null } }, g))
    await new Promise((r) => setTimeout(r, 0))
    assert.ok(!chamadas.slice(n).some((c) => c.url.endsWith('/sac')), 'sem caso, sem GET /sac')
  }
}

;(async () => {
  await testarPagina()
  await testarResumo()
  await testarFormulario()
  await testarDetalhe()
  await testarVincular()
  await testarBloco()
  await testarSac()
  if (pendentesPy.length) {
    console.error(`FALTA NO BACKEND (${pendentesPy.length}) — a tela já está no contrato de 06/11/2026; o que o servidor ainda não tem:`)
    for (const x of pendentesPy) console.error(`  - ${x}`)
    process.exit(1)
  }
  console.log('ok: garantias-sfc')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
