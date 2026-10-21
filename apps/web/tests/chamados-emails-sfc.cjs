// node tests/chamados-emails-sfc.cjs — Pós-venda › Chamados × e-mail (21/10/2026, decisão 3 do Eduardo).
// SÓ ACRESCENTA (regra do dono, 21/10): o e-mail de uma pasta com a categoria "Chamado (Pós-venda)"
// fica ligado ao chamado pelo lado do e-mail, e o detalhe do chamado ganha um bloco NOVO, só de
// leitura, "E-mails ligados" — o histórico, a lista e o resto da aba ficam como eram. O texto
// PROTEGIDO vem de GET /api/chamados/{id}/emails só pra quem lê aquele e-mail no Atendimento; os
// outros veem que o e-mail existe e mais nada. Texto puro (nenhum link clicável); o anexo baixa
// pela rota nova. O contrato é conferido contra apps/api/app/routers/chamados_emails.py e
// apps/api/app/services/mail_atendimento/posvenda.py.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const REL = '../components/ChamadosPainel.vue'
const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

// ─── o SFC de verdade: parse + script + template sem erro ────────────────
const filename = path.resolve(__dirname, REL)
const { descriptor, errors } = parse(ler(REL), { filename })
assert.deepEqual(errors, [], 'ChamadosPainel.vue: parse')
const script = compileScript(descriptor, { id: 'chamados', inlineTemplate: false })
const tpl = compileTemplate({
  source: descriptor.template.content, filename, id: 'chamados',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(tpl.errors, [], 'ChamadosPainel.vue: template compila')
const render = {}
new Function('exports', 'require', transpile(tpl.code))(render, require)

// ─── harness: o <script setup> com as auto-importações falsas ────────────
// O `await` do topo do setup vira `withAsyncContext`, que exige uma instância: aqui,
// fora do Vue, só espera a promessa.
const VueSemInstancia = { ...Vue, withAsyncContext: (fn) => [fn(), () => {}] }
const icone = { render: () => Vue.h('i') }
const casca = { setup: (_p, { slots }) => () => Vue.h('div', slots.default?.()) }
function requerer(nome) {
  if (nome === 'vue') return VueSemInstancia
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => icone })
  if (nome === 'reka-ui') return new Proxy({}, { get: () => casca })
  return require(nome)
}
const AUTO = ['ref', 'computed', 'reactive', 'watch', 'nextTick', 'useApi', 'useToasts', 'useAuthStore', 'useCan', 'useRoute']
async function montar(g) {
  const mod = { exports: {} }
  const valores = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    useApi: () => ({ api: g.api }),
    useToasts: () => ({ success() {}, error() {}, info() {}, warning() {} }),
    useAuthStore: () => ({ user: g.user }),
    useCan: (r, a) => Vue.computed(() => g.user?.role === 'admin' || g.user?.permissions?.[r]?.[a] === true),
    useRoute: () => ({ query: {} }),
  }
  new Function('require', 'module', 'exports', ...AUTO, transpile(script.content))(
    requerer, mod, mod.exports, ...AUTO.map((n) => valores[n]),
  )
  const C = { ...mod.exports.default, render: render.render }
  const escopo = Vue.effectScope()
  const estado = Vue.proxyRefs(
    await escopo.run(() => C.setup(Vue.reactive({}), { emit() {}, expose() {}, attrs: {}, slots: {} })),
  )
  return { estado, mesmo: { ...C, setup: () => estado } }
}
const STUBS = {
  Button: { props: ['disabled', 'size', 'variant', 'type'], setup: (p, { slots, attrs }) => () => Vue.h('button', { ...attrs, disabled: p.disabled }, slots.default?.()) },
  NuxtLink: { props: ['to'], setup: (_p, { slots, attrs }) => () => Vue.h('a', attrs, slots.default?.()) },
  PageHeader: { props: ['title', 'description'], setup: (p, { slots }) => () => Vue.h('header', [p.title, slots.actions?.()]) },
  StatCard: { render: () => null },
  ChamadosIa: { render: () => null },
  InformarThreemaModal: { render: () => null },
  ObservacaoPopover: { render: () => null },
}
async function html(caixa) {
  const app = Vue.createSSRApp(caixa.mesmo)
  for (const [n, c] of Object.entries(STUBS)) app.component(n, c)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
const so = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
const espera = () => new Promise((r) => setTimeout(r, 0))

// ─── o mundo ─────────────────────────────────────────────────────────────
const ADMIN = { id: 'a', role: 'admin', email: 'a@x', permissions: {} }
const LINHA = {
  id: 'ch1', data: '2026-10-21', pedido_bling: '290001', pedido_marketplace: '2000012345678901',
  plataforma: 'mercadolivre', conta: 'Barbosa', produto: 'airfryer', sku: 'uaf001', status_bling: null,
  status_bling_atual: 'Em aberto', origem: 'devolucao', origem_ref: null, chamado: null, chamado_url: null,
  canal: 'manual', chamado_de_tela: false, leitura_robo_at: null, alterar_status_bling: null,
  valor_recuperado: null, auto_ligada: false, auto_dias: null, auto_mensagem: null, auto_ultimo_envio_at: null,
  auto_proximo_envio_at: null, resolvido: false, resolvido_at: null, observacao: null,
  created_at: '2026-10-21T13:00:00Z', updated_at: '2026-10-21T13:00:00Z', mensagens_total: 1,
  ultima_mensagem_at: null, status_plataforma: null, status_plataforma_at: null, status_aba: 'analise_humano',
  status_aba_at: null, ultima_resposta_at: null, ultima_resposta_direcao: null, ultima_resposta_autor: null, anexos_auto: [],
}
const MENSAGENS = [{
  id: 'm1', chamado_id: 'ch1', direcao: 'sistema', tipo: 'sistema', canal: 'manual', status: 'registrada',
  erro: null, autor_nome: 'sistema', enviada_at: null, anexos: [], created_at: '2026-10-21T13:00:00Z',
  texto: 'Chamado registrado (origem devolucao) por Ana',
}]
const EMAILS = {
  total: 2, ocultos: 1,
  itens: [
    {
      id: 'e1', chamado_id: 'ch1', recebido_em: '2026-10-21T12:55:00Z', pasta: 'problema ml',
      loja: 'Barbosa', plataforma: 'ml', pedido: '2000012345678901', pode_ler: true, de: 'maria@gmail.com',
      de_nome: 'Maria', assunto: 'Produto chegou quebrado', texto: 'Veja em https://exemplo.com/foto', links_removidos: 1,
      codigo_mascarado: false, anexos: [{ id: 'a1', filename: 'foto.jpg', content_type: 'image/jpeg', tamanho: 5 }],
      suspeito: false, suspeito_motivos: [],
    },
    {
      id: 'e2', chamado_id: 'ch1', recebido_em: '2026-10-21T14:00:00Z', pasta: 'problema ml',
      loja: 'Barbosa', plataforma: 'ml', pedido: null, pode_ler: false,
    },
  ],
}
const LISTA = { items: [], total: 0, limit: 100, offset: 0, plataformas: [], contas: [], nomes: [] }

;(async () => {
  // ═══════════════════════════════ abrir o histórico carrega o bloco; o histórico não muda
  {
    const chamadas = []
    const caixa = await montar({
      user: ADMIN,
      api: async (url) => {
        chamadas.push(url)
        if (url === '/api/chamados/ch1/mensagens') return MENSAGENS
        if (url === '/api/chamados/ch1/emails') return EMAILS
        return LISTA
      },
    })
    const e = caixa.estado
    await e.openHistorico(LINHA)
    await espera()
    assert.ok(chamadas.includes('/api/chamados/ch1/emails'), 'abrir o histórico busca /emails')
    assert.equal(e.emailsLigados.itens.length, 2)
    assert.equal(e.emailsLigados.ocultos, 1)
    // o histórico é o de sempre: nenhum balão novo, os mesmos tipos de antes
    assert.deepEqual(e.bolhas.map((b) => b.lado), ['sistema'])

    const h = await html(caixa)
    const t = so(h)
    assert.match(t, /E-mails ligados \(2\)/)
    assert.match(t, /1 sem acesso ao texto/)
    assert.match(t, /Maria · pasta «problema ml» · Barbosa/)
    assert.match(t, /Produto chegou quebrado/)
    assert.match(t, /Veja em https:\/\/exemplo\.com\/foto/)
    assert.ok(!h.includes('href="https://exemplo.com/foto"'), 'o link do e-mail é texto, não clicável')
    assert.ok(h.includes('href="/api/chamados/emails/anexos/a1"'), 'o anexo baixa pela rota nova')
    assert.match(t, /1 link\(s\) de acesso removido\(s\)/)
    assert.match(t, /sem acesso ao texto — só quem vê o Atendimento desta loja/)
    assert.match(t, /Chamado registrado \(origem devolucao\) por Ana/, 'o histórico continua')
    assert.ok(h.includes('<details') && h.includes(' open'), 'até 3 e-mails o bloco vem aberto')
    assert.ok(!h.includes('data-email-suspeito'), 'o e-mail de remetente normal não tem o selo')

    // fechar limpa; o bloco some
    e.closeHistorico()
    await espera()
    assert.equal(e.emailsLigados.itens.length, 0)
  }

  // ═══════════════════════════════ sem e-mail ligado (ou /emails falhou): nada muda na tela
  for (const resposta of [{ total: 0, ocultos: 0, itens: [] }, new Error('500')]) {
    const caixa = await montar({
      user: ADMIN,
      api: async (url) => {
        if (url === '/api/chamados/ch1/mensagens') return MENSAGENS
        if (url === '/api/chamados/ch1/emails') {
          if (resposta instanceof Error) throw resposta
          return resposta
        }
        return LISTA
      },
    })
    await caixa.estado.openHistorico(LINHA)
    await espera()
    assert.equal(caixa.estado.hist.erro, null, 'o histórico não mostra erro dos e-mails')
    const t = so(await html(caixa))
    assert.ok(!t.includes('E-mails ligados'))
    assert.match(t, /Chamado registrado \(origem devolucao\) por Ana/)
  }

  // ═══════════════════════════════ o remetente SUSPEITO (revisão de 21/10): o selo, com os motivos
  {
    const suspeito = {
      total: 1, ocultos: 0,
      itens: [{
        ...EMAILS.itens[0], id: 'e9', de: 'suporte@mercadolivre-br.com', de_nome: 'Mercado Livre', anexos: [],
        suspeito: true, suspeito_motivos: ['o remetente se diz da plataforma, mas o domínio não é o oficial'],
      }],
    }
    const caixa = await montar({
      user: ADMIN,
      api: async (url) => (url.endsWith('/mensagens') ? MENSAGENS : url.endsWith('/emails') ? suspeito : LISTA),
    })
    await caixa.estado.openHistorico(LINHA)
    await espera()
    const h = await html(caixa)
    assert.match(so(h), /Mercado Livre · pasta «problema ml» · Barbosa remetente suspeito/)
    assert.ok(h.includes('data-email-suspeito'))
    assert.ok(h.includes('title="o remetente se diz da plataforma, mas o domínio não é o oficial"'), 'os motivos na dica')
  }

  // ═══════════════════════════════ muitos e-mails: o bloco vem recolhido
  {
    const muitos = { total: 4, ocultos: 4, itens: [1, 2, 3, 4].map((i) => ({ ...EMAILS.itens[1], id: `x${i}` })) }
    const caixa = await montar({
      user: ADMIN,
      api: async (url) => (url.endsWith('/mensagens') ? MENSAGENS : url.endsWith('/emails') ? muitos : LISTA),
    })
    await caixa.estado.openHistorico(LINHA)
    await espera()
    const h = await html(caixa)
    assert.match(so(h), /E-mails ligados \(4\)/)
    assert.ok(!/<details[^>]* open/.test(h), 'mais de 3: recolhido')
  }

  // ═══════════════════════════════ o contrato com a API
  {
    const rotas = api('routers/chamados_emails.py')
    assert.match(rotas, /@router\.get\("\/\{chamado_id\}\/emails"\)/)
    assert.match(rotas, /@router\.get\("\/emails\/anexos\/\{attachment_id\}"\)/)
    const svc = api('services/mail_atendimento/posvenda.py')
    for (const campo of ['"pode_ler"', '"recebido_em"', '"pasta"', '"loja"', '"de_nome"', '"assunto"', '"texto"', '"links_removidos"', '"codigo_mascarado"', '"anexos"', '"ocultos"', '"suspeito"', '"suspeito_motivos"']) {
      assert.ok(svc.includes(campo), `posvenda devolve ${campo}`)
    }
  }

  console.log('chamados-emails-sfc: ok')
})().catch((err) => {
  console.error(err)
  process.exit(1)
})
