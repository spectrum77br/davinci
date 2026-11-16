// node tests/devolucao-lancar.cjs
// Bloco de LANÇAMENTO da janela do painel de Devoluções (16/11/2026, Vinicius:
// "precisamos colocar o lançar tudo já nesse painel"). O bloco carrega as
// regras da tela antiga (pages/devolucoes-antigo.vue): travas de link e de
// vídeo, estoque no Bling pelos modais, manutenção, fotos, lixeira com estorno.
//
// Monta o components/DevolucaoLancar.vue DE VERDADE no jsdom, com API falsa e
// modais dublês, e confere os corpos de POST/PATCH e as travas.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const base = path.join(__dirname, '..')
const req = (n) => require(require.resolve(n, { paths: [base] }))
const { JSDOM } = req('jsdom')
const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://davinci.invalid/' })
for (const n of ['window', 'document', 'Document', 'ShadowRoot', 'Element', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'SVGElement', 'Node', 'Text', 'Comment', 'Event', 'MouseEvent', 'KeyboardEvent', 'FormData', 'File']) globalThis[n] = n === 'window' ? window : window[n]
const ts = req('typescript')
const Vue = req('vue')
const { parse, compileScript } = req('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const file = path.join(base, 'components/DevolucaoLancar.vue')
const { descriptor, errors } = parse(fs.readFileSync(file, 'utf8'), { filename: file })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'lancar', inlineTemplate: true })
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
function requerer(nome) {
  if (nome === 'vue') return Vue
  if (nome === 'lucide-vue-next') return icones
  if (nome === '~/lib/linkMega') {
    const m = { exports: {} }
    new Function('require', 'module', 'exports', transpile(fs.readFileSync(path.join(base, 'lib/linkMega.ts'), 'utf8')))(require, m, m.exports)
    return m.exports
  }
  throw new Error(`import inesperado: ${nome}`)
}
const mod = { exports: {} }
new Function('require', 'module', 'exports', transpile(script.content.replace(/import\.meta\.server/g, 'false')))(requerer, mod, mod.exports)
const Componente = mod.exports.default

const tick = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick(); await new Promise((r) => setTimeout(r, 0)) } }

// ── ambiente falso ──────────────────────────────────────────────────────────
let email = 'alguem@tuta.com'
let avisos = []
let fechados = []
let seqAviso = 0
let chamadas = []
let rotas = []
let perguntas = []
let respostaConfirm = true
let respostaPrompt = null
let modalResposta = {}
Object.assign(globalThis, {
  ref: Vue.ref, reactive: Vue.reactive, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick,
  onMounted: Vue.onMounted, onBeforeUnmount: Vue.onBeforeUnmount,
  useAuthStore: () => ({ get user() { return { email } } }),
  useToasts: () => ({
    push: (t, ttl) => { const id = ++seqAviso; avisos.push({ id, ...t, ttl }); return id },
    dismiss: (id) => { fechados.push(id) },
  }),
  useApi: () => ({
    api: async (url, opts = {}) => {
      const metodo = opts.method || 'GET'
      const body = opts.body instanceof window.FormData ? { __arquivo: opts.body.get('file').name } : (opts.body === undefined ? undefined : JSON.parse(JSON.stringify(opts.body)))
      chamadas.push({ url, metodo, body })
      const r = rotas.find((x) => x.metodo === metodo && (typeof x.url === 'string' ? x.url === url : x.url.test(url)))
      if (!r) throw new Error(`rota inesperada: ${metodo} ${url}`)
      const out = typeof r.resposta === 'function' ? r.resposta({ url, body }) : r.resposta
      if (out && out.__erro) { const e = new Error('falhou'); e.data = { detail: out.__erro }; throw e }
      return JSON.parse(JSON.stringify(out))
    },
  }),
  confirm: (t) => { perguntas.push(t); return respostaConfirm },
})
window.prompt = (t, sugerido) => { perguntas.push(t); return respostaPrompt === '__sugerido' ? sugerido : respostaPrompt }

// Modal dublê: aberto, responde sozinho (confirm com a resposta programada, ou cancel).
const modalDuble = (nome) => Vue.defineComponent({
  name: nome,
  props: ['open', 'sku', 'soldSku', 'condicao', 'fullDestino'],
  emits: ['confirm', 'cancel'],
  setup(p, { emit }) {
    Vue.watch(() => p.open, (v) => {
      if (!v) return
      const r = modalResposta[nome]
      modalResposta.abertos = [...(modalResposta.abertos || []), { nome, sku: p.sku ?? p.soldSku, condicao: p.condicao }]
      queueMicrotask(() => (r === undefined || r === null ? emit('cancel') : emit('confirm', r)))
    })
    return () => null
  },
})

// Relógio falso do componente (o de 60 s da conferência): o teste dispara na mão.
let relogios = []
window.setTimeout = (fn, ms) => { relogios.push({ fn, ms, vivo: true }); return relogios.length }
window.clearTimeout = (id) => { if (relogios[id - 1]) relogios[id - 1].vivo = false }

const linha = (over = {}) => ({
  id: 'L1', data: '2026-11-08T12:00:00Z', pedido_bling: '306120', pedido_marketplace: 'MKT1', conta: 'vortan', cliente: 'Marcos',
  situacao_bling_id: 1, situacao_bling_nome: 'Aguardando Devolução', sku: 'f105.ci', produtos: 'Fossibot F105', custo_produto: 612,
  condicao_produto: null, link_abertura: null, reembolso: false, motivo_devolucao: null, link_envio: null, custo_manutencao: null,
  tecnico: null, devolver_estoque: false, manutencao: false, observacao: null, troca_sku: null, troca_condicao: null,
  estoque_suffix: null, quantidade: 1, estoque_destino_sku: null, estoque_nova_tag: null, manutencao_destino: null, tag: 'ci',
  data_devolvido_estoque: null, prazo: null, prazo_contestacao: null, estoque_mov_sku: null, estoque_mov_bling_id: null,
  estoque_mov_action: null, estoque_mov_qty: null, estoque_mov_revertido_at: null, estoque_status: null, estoque_erro: null,
  created_at: '2026-11-16T10:00:00Z', updated_at: '2026-11-16T10:00:00Z', anexos: [], ...over,
})
const peca = (over = {}) => ({
  data: '2026-11-08T12:00:00Z', pedido_bling: '306120', pedido_marketplace: 'MKT1', conta: 'vortan', sku: 'b056.24', produtos: 'Mala 24',
  quantidade: 1, custo_produto: 100, nome_destinatario: 'Marcos', cep_destino: '60000000', ja_devolvido: false, ...over,
})

async function montar({ pecas = [], linhas = [], props = {} } = {}) {
  avisos = []; fechados = []; chamadas = []; perguntas = []; modalResposta = {}; respostaConfirm = true; respostaPrompt = null
  rotas = [
    { metodo: 'GET', url: /^\/api\/devolutions\/order-lookup\?pedido=/, resposta: () => pecas },
    { metodo: 'GET', url: /^\/api\/devolutions\?search=/, resposta: () => ({ items: linhas }) },
  ]
  const mudou = []
  const el = window.document.createElement('div')
  window.document.body.appendChild(el)
  const estado = Vue.reactive({ pedido: '306120', fila: 'acompanhamento', videoLink: null, canEdit: true, canDelete: true, isAdmin: true, ...props })
  const alvo = Vue.ref(null)
  const app = Vue.createApp({ render: () => Vue.h(Componente, { ...estado, ref: alvo, onMudou: () => mudou.push(1) }) })
  for (const n of ['DevolucaoManutencaoModal', 'DevolucaoTrocaSkuModal', 'DevolucaoEstoqueModal']) app.component(n, modalDuble(n))
  const erros = []
  app.config.errorHandler = (e) => { erros.push(e) }
  app.config.warnHandler = (m) => { erros.push(new Error(`aviso do Vue: ${m}`)) }
  app.mount(el)
  await tick()
  const cartoes = () => [...el.querySelectorAll('[data-lancamento]')]
  const campo = (cartao, rotulo) => {
    const span = [...cartao.querySelectorAll('span')].find((s) => s.textContent.trim() === rotulo)
    assert.ok(span, `campo ${rotulo}`)
    return span.parentElement.querySelector('select, input, textarea')
  }
  const escolher = async (sel, valor) => { sel.value = valor; sel.dispatchEvent(new window.Event('change', { bubbles: true })); await tick() }
  const digitar = async (inp, valor) => { inp.value = valor; inp.dispatchEvent(new window.Event('input', { bubbles: true })); await tick() }
  const botao = (raiz, texto) => [...raiz.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(texto))
  const clicar = async (b) => { assert.ok(b, 'botão existe'); assert.equal(b.disabled, false, 'botão habilitado'); b.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await tick() }
  const fim = () => { app.unmount(); el.remove(); assert.deepEqual(erros.map(String), [], 'sem erro/aviso do Vue') }
  // Links e fotos moram em Provas (na janela): chegam ao bloco como props.
  const provas = async (campos) => { Object.assign(estado, campos); await tick() }
  return { el, estado, mudou, cartoes, campo, escolher, digitar, botao, clicar, fim, alvo, provas }
}
const corpo = (metodo, re) => chamadas.filter((c) => c.metodo === metodo && re.test(c.url)).map((c) => c.body)
const titulos = () => avisos.map((a) => a.title)

;(async () => {
  // 1 ─ carga: só o pedido da janela; ja_devolvido fora; lançado antes do rascunho; vídeo no Link envio
  {
    const t = await montar({
      pecas: [peca({ ja_devolvido: true }), peca(), peca({ sku: 'a006+a015', custo_produto: 30, produtos: 'Kit' }), peca({ pedido_bling: '999999', sku: 'x1' })],
      linhas: [linha(), linha({ id: 'OUTRO', pedido_bling: '3061200' })],
      props: { videoLink: 'https://drive.google.com/file/d/abc' },
    })
    const cs = t.cartoes()
    assert.deepEqual(cs.map((c) => c.getAttribute('data-lancamento')), ['L1', 'rascunho', 'rascunho', 'rascunho'])
    assert.match(cs[0].textContent, /lançada 16\/11/)
    assert.match(cs[1].textContent, /falta lançar/)
    assert.match(cs[2].textContent, /a006/); assert.match(cs[3].textContent, /a015/)
    assert.equal(t.campo(cs[2], 'Custo do produto').value, '15', 'kit a+b: custo dividido')
    // os links e as fotos não ficam mais nos cartões (moram em Provas, na janela)
    assert.equal(/Link envio|Link abertura|escolher fotos|anexar foto/.test(t.el.textContent), false)
    assert.equal(t.el.querySelector('input[type=file]'), null)
    assert.ok(t.botao(t.el, 'lançar todas as peças'), '2+ rascunhos: botão do topo')
    // seletor de condição: Novo, Usado, Perdimento(=Sucata) + outros casos; sem Manutenção
    const ops = [...t.campo(cs[1], 'Condição').querySelectorAll('option')].filter((o) => !o.disabled).map((o) => `${o.value}|${o.textContent.trim()}`)
    assert.deepEqual(ops, ['|—', 'Novo|Novo', 'Usado|Usado', 'Sucata|Perdimento', 'Extraviado|Extraviado', 'Não devolvido|Não devolvido', 'Trocado|Trocado'])
    assert.equal(t.campo(cs[1], 'Condição').querySelector('optgroup').label, 'outros casos')
    assert.deepEqual([...t.campo(cs[1], 'Motivo').querySelectorAll('option')].length, 8)
    assert.equal(t.botao(cs[0], 'salvar'), undefined, 'sem mudança: sem salvar')
    t.fim()
  }

  // 2 ─ rascunho: travas (as da tela antiga) e Perdimento grava Sucata
  {
    const t = await montar({ pecas: [peca()] })
    const [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Sucata')
    await t.clicar(t.botao(c, 'lançar'))
    assert.equal(corpo('POST', /devolutions$/).length, 0)
    assert.match(c.textContent, /Vídeo da abertura obrigatório para Extraviado \/ Perdimento \/ Manutenção: cole o link em Provas/)
    assert.deepEqual(titulos(), ['Peça não lançada'])
    // mala + Golpe sem link de envio
    await t.provas({ linkAbertura: 'https://x.y/abertura' })
    await t.escolher(t.campo(c, 'Motivo'), 'Golpe')
    await t.clicar(t.botao(c, 'lançar'))
    assert.match(c.textContent, /Vídeo do envio obrigatório \(mala\/eletro com motivo que abre chamado\): cole o link em Provas/)
    await t.provas({ linkEnvio: 'nao ha' })
    await t.clicar(t.botao(c, 'lançar'))
    assert.match(c.textContent, /Link de envio precisa ser um endereço http\(s\)/)
    await t.provas({ linkEnvio: 'https://drive.google.com/x' })
    await t.clicar(t.botao(c, 'lançar'))
    assert.match(c.textContent, /Link envio: Cole o link do vídeo na MEGA/)
    assert.equal(corpo('POST', /devolutions$/).length, 0)
    await t.provas({ linkEnvio: 'https://mega.nz/file/AbCdEfGh#0123456789abcdefghijklmnopqrstuvwxyzABCDEFG' })
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'N1', ...body, reembolso: true }) })
    await t.clicar(t.botao(c, 'lançar'))
    const [b] = corpo('POST', /devolutions$/)
    assert.equal(b.condicao_produto, 'Sucata'); assert.equal(b.reembolso, true, 'Sucata liga reembolso'); assert.equal(b.devolver_estoque, false)
    assert.equal(b.motivo_devolucao, 'Golpe'); assert.equal(b.custo_produto, 100); assert.equal(b.quantidade, 1)
    assert.equal(b.link_abertura, 'https://x.y/abertura', 'o link colado em Provas vai no lançamento')
    assert.equal(b.link_envio, 'https://mega.nz/file/AbCdEfGh#0123456789abcdefghijklmnopqrstuvwxyzABCDEFG')
    assert.equal(b.pedido_bling, '306120'); assert.equal(b.sku, 'b056.24'); assert.equal(b.conta, 'vortan'); assert.equal(b.estoque_destino_sku, null)
    assert.deepEqual(modalResposta.abertos, undefined, 'Sucata não abre modal')
    assert.equal(t.mudou.length, 1)
    assert.deepEqual(t.cartoes().map((x) => x.getAttribute('data-lancamento')), ['N1'])
    assert.ok(titulos().includes('Devoluções adicionadas'))
    t.fim()
  }

  // 3 ─ rascunho Novo (não mala): modal de destino → POST com extras e devolver_estoque; fotos sobem depois; modal cancelado mantém
  {
    const t = await montar({ pecas: [peca({ sku: 'f105.ci', custo_produto: 612 })] })
    let [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Novo')
    // foto escolhida em Provas antes de existir lançamento: chega como prop e sobe com o 1º
    let fotosEnviadas = 0
    await t.provas({ fotos: [new window.File(['x'], 'foto1.jpg', { type: 'image/jpeg' })], onFotosEnviadas: () => { fotosEnviadas += 1 } })
    assert.equal(await t.alvo.value.anexarFotos([new window.File(['y'], 'outra.jpg', { type: 'image/jpeg' })]), false, 'sem lançamento: o pai segura as fotos')
    await t.clicar(t.botao(c, 'lançar')) // modal cancela (sem resposta programada)
    assert.equal(corpo('POST', /devolutions$/).length, 0, 'cancelado: nada criado')
    assert.deepEqual(t.cartoes().map((x) => x.getAttribute('data-lancamento')), ['rascunho'])
    assert.equal(modalResposta.abertos[0].nome, 'DevolucaoEstoqueModal'); assert.equal(modalResposta.abertos[0].sku, 'f105.ci'); assert.equal(modalResposta.abertos[0].condicao, 'Novo')
    modalResposta.DevolucaoEstoqueModal = { destino_sku: 'f105.sa' }
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'N2', ...body, estoque_status: 'ok', estoque_mov_sku: 'f105.sa', estoque_mov_qty: 1, data_devolvido_estoque: '2026-11-16T11:00:00Z', bling_stock_result: { ok: true, action: 'stock_added', sku: 'f105.sa', bling_product_id: 9, message: '+1 em f105.sa' } }) })
    rotas.push({ metodo: 'POST', url: '/api/devolutions/N2/anexos', resposta: () => linha({ id: 'N2', condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'ok', estoque_mov_sku: 'f105.sa', chamado_ml_status: 'enviada', chamado_plataforma: 'shopee', chamado_enviada_at: '2026-11-10T10:00:00Z', anexos: [{ id: 'A1', filename: 'foto1.jpg', content_type: 'image/jpeg', size_bytes: 1, ml_file_name: null, created_at: '2026-11-16T11:00:01Z' }] }) })
    c = t.cartoes()[0]
    await t.clicar(t.botao(c, 'lançar'))
    const [b] = corpo('POST', /devolutions$/)
    assert.equal(b.condicao_produto, 'Novo'); assert.equal(b.devolver_estoque, true); assert.equal(b.estoque_destino_sku, 'f105.sa'); assert.equal(b.reembolso, false)
    assert.equal(b.estoque_nova_tag, null); assert.equal(b.estoque_suffix, null); assert.equal(b.troca_sku, null)
    assert.deepEqual(corpo('POST', /anexos$/), [{ __arquivo: 'foto1.jpg' }])
    assert.ok(titulos().includes('Bling · estoque atualizado'))
    assert.ok(titulos().some((x) => /fotos não foram pra contestação/.test(x)), 'aviso das fotos depois da contestação')
    c = t.cartoes()[0]
    assert.equal(c.getAttribute('data-lancamento'), 'N2')
    assert.match(c.textContent, /estoque ok/); assert.match(c.textContent, /f105\.sa/)
    assert.equal(fotosEnviadas, 1, 'avisa o pai pra esvaziar a fila de Provas')
    assert.match(c.textContent, /A contestação já saiu/); assert.match(c.textContent, /1 foto\(s\) deste pedido foram anexadas depois/)
    assert.match(c.textContent, /Shopee: aberto/)
    t.fim()
  }

  // 4 ─ mala Novo: sem modal, destino = o próprio SKU; "lançar todas" valida tudo antes
  {
    const t = await montar({ pecas: [peca(), peca({ sku: 'b057.28' })] })
    const cs = t.cartoes()
    await t.escolher(t.campo(cs[0], 'Condição'), 'Novo')
    await t.escolher(t.campo(cs[1], 'Condição'), 'Extraviado')
    await t.clicar(t.botao(t.el, 'lançar todas as peças'))
    assert.equal(corpo('POST', /devolutions$/).length, 0, 'uma inválida barra todas (como antes)')
    assert.match(t.cartoes()[1].textContent, /Vídeo da abertura obrigatório/)
    await t.provas({ linkAbertura: 'https://x.y/z' })
    let n = 0
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: `M${++n}`, ...body, created_at: `2026-11-16T10:0${n}:00Z` }) })
    await t.clicar(t.botao(t.el, 'lançar todas as peças'))
    const bs = corpo('POST', /devolutions$/)
    assert.equal(bs.length, 2)
    assert.equal(bs[0].estoque_destino_sku, 'b056.24'); assert.equal(bs[0].devolver_estoque, true)
    assert.equal(bs[1].condicao_produto, 'Extraviado'); assert.equal(bs[1].reembolso, true); assert.equal(bs[1].devolver_estoque, false)
    assert.equal(modalResposta.abertos, undefined)
    assert.equal(t.mudou.length, 1, 'um aviso ao pai no fim do lote')
    assert.deepEqual(t.cartoes().map((x) => x.getAttribute('data-lancamento')), ['M1', 'M2'])
    t.fim()
  }

  // 5 ─ lançado: salvar explícito, corpo do PATCH, legado do link_envio
  {
    const t = await montar({ linhas: [linha({ link_envio: 'nao ha', condicao_produto: 'Não devolvido', observacao: 'obs' })] })
    let [c] = t.cartoes()
    await t.digitar(t.campo(c, 'Observação'), 'obs nova')
    assert.ok(t.botao(c, 'salvar')); assert.ok(t.botao(c, 'desfazer')); assert.match(c.textContent, /não salvo/)
    assert.equal(chamadas.filter((x) => x.metodo === 'PATCH').length, 0, 'nada salva sozinho')
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => { const { link_envio, ...r } = body; return linha({ link_envio: 'nao ha', ...r }) } })
    await t.clicar(t.botao(c, 'salvar'))
    let [b] = corpo('PATCH', /L1$/)
    assert.equal('link_envio' in b, false, 'texto legado não tocado não vai no corpo')
    assert.deepEqual(Object.keys(b).sort(), ['condicao_produto', 'custo_manutencao', 'custo_produto', 'devolver_estoque', 'estoque_destino_sku', 'estoque_nova_tag', 'estoque_suffix', 'link_abertura', 'manutencao_destino', 'motivo_devolucao', 'observacao', 'quantidade', 'reembolso', 'tecnico', 'troca_condicao', 'troca_sku'])
    assert.equal(b.observacao, 'obs nova'); assert.equal(b.condicao_produto, 'Não devolvido'); assert.equal(b.custo_produto, 612); assert.equal(b.quantidade, 1)
    assert.equal(t.mudou.length, 1)
    c = t.cartoes()[0]
    assert.equal(t.botao(c, 'salvar'), undefined, 'salvo: botão some')
    // link novo colado em Provas: texto que não é endereço é recusado, sem ir ao servidor
    assert.match(await t.alvo.value.gravarLink('link_envio', 'nao ha mesmo'), /Link de envio precisa ser um endereço http\(s\)/)
    assert.match(await t.alvo.value.gravarLink('link_envio', 'https://drive.google.com/x'), /Vídeo do envio: Cole o link do vídeo na MEGA/)
    assert.equal(corpo('PATCH', /L1$/).length, 1)
    assert.equal(t.botao(t.cartoes()[0], 'salvar'), undefined)
    // custo com vírgula
    await t.digitar(t.campo(t.cartoes()[0], 'Custo do produto'), '1.234,56')
    await t.clicar(t.botao(t.cartoes()[0], 'salvar'))
    assert.equal(corpo('PATCH', /L1$/)[1].custo_produto, 1234.56)
    await t.digitar(t.campo(t.cartoes()[0], 'Custo do produto'), 'abc')
    await t.clicar(t.botao(t.cartoes()[0], 'salvar'))
    assert.equal(corpo('PATCH', /L1$/).length, 2, 'número inválido não salva')
    t.fim()
  }

  // 6 ─ lançado: mudar condição → modais + devolver_estoque + aviso do Bling; cancelar não salva
  {
    const t = await montar({ linhas: [linha()] })
    let [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Usado')
    await t.clicar(t.botao(c, 'salvar')) // cancela o modal
    assert.equal(chamadas.filter((x) => x.metodo === 'PATCH').length, 0)
    assert.equal(t.campo(t.cartoes()[0], 'Condição').value, 'Usado', 'cancelado: o cartão fica como a pessoa deixou')
    modalResposta.DevolucaoEstoqueModal = { nova_tag: 'eletro' }
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => linha({ ...body, estoque_status: 'falhou', estoque_erro: 'Bling 429', estoque_conferir_action: 'auto_retry', estoque_auto_tentativas: 1, bling_stock_result: { ok: false, action: 'error', sku: null, bling_product_id: null, message: 'Bling recusou' } }) })
    await t.clicar(t.botao(t.cartoes()[0], 'salvar'))
    const [b] = corpo('PATCH', /L1$/)
    assert.equal(b.condicao_produto, 'Usado'); assert.equal(b.devolver_estoque, true); assert.equal(b.estoque_nova_tag, 'eletro'); assert.equal(b.estoque_destino_sku, null); assert.equal(b.reembolso, false)
    const info = avisos.find((a) => a.title === 'Atualizando o estoque no Bling…')
    assert.ok(info && info.ttl === 0 && fechados.includes(info.id), 'aviso fixo aberto e fechado na resposta')
    assert.ok(titulos().includes('Bling ocupado · o DaVinci tenta de novo sozinho'))
    c = t.cartoes()[0]
    assert.match(c.textContent, /tentando sozinho/); assert.match(c.textContent, /Estoque NÃO entrou/); assert.match(c.textContent, /tentando de novo sozinho \(1 de 5\)/)
    assert.ok(t.botao(c, 'Tentar de novo'))
    // Tentar de novo: pergunta + corpo
    rotas.push({ metodo: 'POST', url: '/api/devolutions/L1/estoque/tentar-de-novo', resposta: () => linha({ condicao_produto: 'Usado', devolver_estoque: true, estoque_nova_tag: 'eletro', estoque_status: 'ok', estoque_mov_sku: 'z0340.eletro', estoque_mov_qty: 1 }) })
    await t.clicar(t.botao(c, 'Tentar de novo'))
    assert.equal(perguntas.at(-1), 'Lançar de novo a entrada de 1 un. (produto z novo) no Bling?')
    assert.deepEqual(corpo('POST', /tentar-de-novo$/), [{ confirmo_conferi_no_bling: false }])
    assert.ok(titulos().includes('Entrada registrada'))
    assert.match(t.cartoes()[0].textContent, /z0340\.eletro/)
    const amb = avisos.find((a) => a.title === 'Bling ocupado · o DaVinci tenta de novo sozinho')
    t.fim()
    assert.ok(fechados.includes(amb.id), 'aviso âmbar fechado ao desmontar')
  }

  // 7 ─ manutenção: pergunta Sim/Não, sair exige custo, reembolso, estoque travado, voltou do técnico só pra quem pode
  {
    email = 'alguem@tuta.com'
    const t = await montar({ linhas: [linha({ condicao_produto: 'Manutenção', link_abertura: 'https://x.y/a', reembolso: true, prazo: '2026-12-16T10:00:00Z', tecnico: 'Shark' })] })
    let [c] = t.cartoes()
    assert.equal(t.campo(c, 'Vai pra manutenção?').value, 'sim')
    assert.equal(t.campo(c, 'Condição').disabled, true)
    assert.match(t.campo(c, 'Condição').selectedOptions[0].textContent, /com o técnico/)
    assert.match(c.textContent, /16\/12\/26/)
    assert.equal(t.botao(c, 'voltou do técnico'), undefined, 'e-mail fora da lista não vê')
    await t.escolher(t.campo(c, 'Vai pra manutenção?'), 'nao')
    await t.escolher(t.campo(c, 'Condição'), 'Sucata')
    await t.clicar(t.botao(c, 'salvar'))
    assert.equal(chamadas.filter((x) => x.metodo === 'PATCH').length, 0)
    assert.ok(titulos().includes('Custo de manutenção obrigatório'))
    await t.digitar(t.campo(c, 'Custo manutenção'), '80,5')
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => linha({ ...body, manutencao: true }) })
    await t.clicar(t.botao(c, 'salvar'))
    const [b] = corpo('PATCH', /L1$/)
    assert.equal(b.condicao_produto, 'Sucata'); assert.equal(b.custo_manutencao, 80.5); assert.equal(b.reembolso, true); assert.equal(b.tecnico, 'Shark')
    assert.equal(modalResposta.abertos, undefined, 'Sucata não abre modal')
    t.fim()

    // quem pode: voltou do técnico → modal Manutenção (Usado) → modal destino → PATCH
    email = 'Sthevem7@tuta.com'
    const u = await montar({ linhas: [linha({ condicao_produto: 'Manutenção', link_abertura: 'https://x.y/a', reembolso: true })] })
    c = u.cartoes()[0]
    modalResposta.DevolucaoManutencaoModal = { tipo: 'Usado' }
    modalResposta.DevolucaoEstoqueModal = { suffix: 'us' }
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => linha({ ...body, manutencao: true, estoque_status: 'conferindo', data_devolvido_estoque: '2026-11-16T12:00:00Z', bling_stock_result: { ok: false, action: 'unconfirmed', sku: null, bling_product_id: null, message: 'pode ter entrado' } }) })
    await u.clicar(u.botao(c, 'voltou do técnico'))
    const [p] = corpo('PATCH', /L1$/)
    assert.equal(p.condicao_produto, 'Manutenção'); assert.equal(p.devolver_estoque, true); assert.equal(p.manutencao_destino, 'Usado'); assert.equal(p.estoque_suffix, 'us')
    assert.deepEqual(modalResposta.abertos.map((m) => `${m.nome}:${m.condicao || ''}`), ['DevolucaoManutencaoModal:', 'DevolucaoEstoqueModal:Usado'])
    assert.ok(titulos().includes('Bling · conferindo a entrada'))
    c = u.cartoes()[0]
    assert.match(c.textContent, /Conferindo no Bling…/)
    assert.equal(u.botao(c, 'Tentar de novo'), undefined, 'conferindo: sem botões')
    // estoque travado: mudar a condição é barrado e o seletor volta
    await u.escolher(u.campo(c, 'Vai pra manutenção?'), 'nao')
    assert.equal(u.campo(u.cartoes()[0], 'Vai pra manutenção?').value, 'sim')
    assert.ok(titulos().includes('Espere a conferência do estoque'))
    // lixeira barrada na conferência
    await u.clicar(c.querySelector('button[title^="Remover lançamento"]'))
    assert.equal(chamadas.filter((x) => x.metodo === 'DELETE').length, 0)
    u.fim()
    email = 'alguem@tuta.com'
  }

  // 8 ─ sem confirmação: Já entrou (prompt com sugestão) e SKU diferente
  {
    const t = await montar({ linhas: [linha({ condicao_produto: 'Usado', devolver_estoque: true, estoque_status: 'sem_confirmacao', estoque_conferir_sku: 'f105.us', estoque_erro: 'sem resposta' })] })
    const [c] = t.cartoes()
    assert.match(c.textContent, /Entrada não confirmada/)
    respostaPrompt = '__sugerido'
    rotas.push({ metodo: 'POST', url: '/api/devolutions/L1/estoque/ja-entrou', resposta: ({ body }) => (body.confirmo_outro_sku ? linha({ estoque_status: 'ok', estoque_mov_sku: 'f105.us' }) : { __erro: { code: 'estoque_sku_diferente', message: 'A entrada foi em X. Confirma?' } }) })
    await t.clicar(t.botao(c, 'Já entrou'))
    assert.match(perguntas[0], /Em qual SKU você viu a entrada "Devolução pedido 306120"/)
    assert.match(perguntas[1], /Só confirme se a entrada foi lançada à mão nesse SKU\./)
    assert.deepEqual(corpo('POST', /ja-entrou$/), [{ sku: 'f105.us' }, { sku: 'f105.us', confirmo_outro_sku: true }])
    assert.match(t.cartoes()[0].textContent, /estoque ok/)
    assert.equal(t.mudou.length, 1)
    t.fim()
  }

  // 9 ─ lixeira: confirmação, resposta, peça volta; sem canDelete não aparece
  {
    let apagada = false
    const t = await montar({ pecas: [peca({ ja_devolvido: true })], linhas: [linha({ sku: 'b056.24', condicao_produto: 'Sucata', tem_chamado: true, chamado_numero: 'C-1', estoque_mov_sku: null })] })
    rotas[0].resposta = () => [peca({ ja_devolvido: !apagada })]
    rotas[1].resposta = () => ({ items: apagada ? [] : [linha({ sku: 'b056.24' })] })
    const [c] = t.cartoes()
    respostaConfirm = false
    await t.clicar(c.querySelector('button[title^="Remover lançamento"]'))
    assert.equal(chamadas.filter((x) => x.metodo === 'DELETE').length, 0)
    assert.match(perguntas[0], /^Remover o lançamento de "b056\.24"\? Esta ação não pode ser desfeita\./)
    assert.match(perguntas[0], /o prejuízo Sucata que o DaVinci lançou sozinho/)
    assert.match(perguntas[0], /ela volta no Bling para a de antes da devolução \(hoje: Aguardando Devolução\)/)
    assert.match(perguntas[0], /o chamado aberto automaticamente é apagado/)
    respostaConfirm = true
    rotas.push({ metodo: 'DELETE', url: '/api/devolutions/L1', resposta: () => { apagada = true; return { ok: true, estoque_estornado: false, mensagem: null, prejuizos_apagados: { qtd: 1, total: 612 }, chamado_apagado: true, situacao_revertida: { de: 'x', para: 1, nome: 'Aguardando Devolução' }, prejuizos_mantidos: [{ tipo: 'Sucata', prejuizo: 10, reembolso: null, motivo: 'conferido' }] } } })
    await t.clicar(c.querySelector('button[title^="Remover lançamento"]'))
    assert.equal(chamadas.filter((x) => x.metodo === 'DELETE').length, 1)
    const ok = avisos.find((a) => a.title === 'Lançamento excluído')
    assert.match(ok.lines[0], /Pedido voltou para Aguardando Devolução · 1 prejuízo apagado \(R\$\s612,00\) · chamado apagado/)
    const nem = avisos.find((a) => a.title === 'Pedido 306120: nem tudo foi desfeito')
    assert.ok(nem && nem.ttl === 0 && nem.kind === 'warning')
    assert.deepEqual(t.cartoes().map((x) => x.getAttribute('data-lancamento')), ['rascunho'], 'a peça excluída volta como rascunho')
    assert.equal(t.mudou.length, 1)
    t.fim()

    const s = await montar({ linhas: [linha()], props: { canDelete: false } })
    assert.equal(s.cartoes()[0].querySelector('button[title^="Remover lançamento"]'), null)
    s.fim()
    // estorno falhou: relê o estoque
    const f = await montar({ linhas: [linha({ estoque_mov_sku: 'f105.ci', estoque_mov_qty: 1 })] })
    rotas.push({ metodo: 'DELETE', url: '/api/devolutions/L1', resposta: () => ({ __erro: { code: 'estoque_estorno_sem_confirmacao', message: 'estorno pode ter saído' } }) })
    rotas.push({ metodo: 'GET', url: '/api/devolutions/L1', resposta: () => linha({ estoque_mov_sku: 'f105.ci', estoque_status: 'sem_confirmacao', estoque_erro: 'estorno incerto' }) })
    await f.clicar(f.cartoes()[0].querySelector('button[title^="Remover lançamento"]'))
    assert.match(perguntas.at(-1), /O estoque devolvido no Bling \(1 un\. de f105\.ci\) será ESTORNADO automaticamente \(saída\)\./)
    assert.match(f.cartoes()[0].textContent, /Estorno não confirmado/)
    assert.match(f.cartoes()[0].textContent, /estorno pode ter saído/)
    assert.ok(f.botao(f.cartoes()[0], 'Estorno já saiu'))
    f.fim()
  }

  // 10 ─ Fraude sem vídeo; valor antigo fora da lista; senha; não-admin; sem editar
  {
    const t = await montar({ pecas: [peca({ sku: 'x100' })], props: { fila: 'fraude', isAdmin: false } })
    const [c] = t.cartoes()
    assert.equal([...c.querySelectorAll('span')].some((s) => s.textContent.trim() === 'Custo do produto'), false, 'custo só admin')
    await t.escolher(t.campo(c, 'Motivo'), 'Golpe')
    await t.clicar(t.botao(c, 'lançar'))
    assert.match(c.textContent, /Vídeo obrigatório: pedido em Fraude/)
    t.estado.videoLink = 'https://drive.google.com/v'; await tick()
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'F1', ...body }) })
    await t.clicar(t.botao(t.cartoes()[0], 'lançar'))
    assert.equal(corpo('POST', /devolutions$/)[0].link_envio, 'https://drive.google.com/v'); assert.equal(corpo('POST', /devolutions$/)[0].custo_produto, 100)
    t.fim()

    const a = await montar({ linhas: [linha({ condicao_produto: 'Entregue', motivo_devolucao: 'Tamanho', senha_status: 'falhou', senha_erro: 'loja deslogada', chamado_plataforma: 'tiktok', tem_chamado: true, chamado_numero: 'C-9', chamado_ml_status: 'pendente', chamado_ml_erro: 'devolucao_sem_foto' })] })
    const [d] = a.cartoes()
    assert.equal(a.campo(d, 'Condição').value, 'Entregue'); assert.equal(a.campo(d, 'Motivo').value, 'Tamanho')
    assert.match(d.textContent, /TikTok: aguardando foto — anexe pela câmera/)
    assert.match(d.textContent, /Senha: não saiu — loja deslogada/)
    rotas.push({ metodo: 'POST', url: '/api/devolutions/L1/senha/pedir-de-novo', resposta: () => linha({ senha_status: 'pendente', senha_erro: null }) })
    await a.clicar(a.botao(d, '↻ pedir de novo'))
    assert.equal(perguntas.at(-1), 'Pedir de novo a senha ao comprador do pedido 306120 no chat da TikTok?\n\nResolva antes o que impediu (ex.: loja deslogada no AdsPower).')
    assert.match(a.cartoes()[0].textContent, /Senha: pedido na fila/)
    a.fim()

    const r = await montar({ pecas: [peca()], linhas: [linha()], props: { canEdit: false } })
    assert.deepEqual([...r.el.querySelectorAll('button:not([data-mostrar])')].map((b) => b.title.slice(0, 18)), ['Remover lançamento'], 'sem editar: só a lixeira (canDelete) — fora o mostrar/esconder, que é só de ver')
    assert.ok([...r.el.querySelectorAll('select, input, textarea')].every((x) => x.disabled))
    r.fim()

    const v = await montar({})
    assert.match(v.el.textContent, /nada pra lançar neste pedido/)
    v.fim()
  }

  // 11 ─ conferência a cada 60 s; erro estoque_conferindo no PATCH relê; recarregar() preserva edição
  {
    relogios = []
    const t = await montar({ linhas: [linha({ condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'conferindo', data_devolvido_estoque: '2026-11-16T12:00:00Z' }), linha({ id: 'L2', sku: 'f106', created_at: '2026-11-16T10:05:00Z' })] })
    const vivos = () => relogios.filter((r) => r.vivo)
    assert.equal(vivos().length, 1); assert.equal(vivos()[0].ms, 60000)
    // edição não salva no L2 + recarregar(): a edição fica
    await t.digitar(t.campo(t.cartoes()[1], 'Observação'), 'digitando')
    rotas[1].resposta = () => ({ items: [linha({ condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'conferindo' }), linha({ id: 'L2', sku: 'f106', created_at: '2026-11-16T10:05:00Z', tem_chamado: true, chamado_numero: 'C-7' })] })
    await t.alvo.value.recarregar(); await tick()
    assert.equal(t.campo(t.cartoes()[1], 'Observação').value, 'digitando', 'recarregar não apaga o que a pessoa digitou')
    assert.match(t.cartoes()[1].textContent, /C-7/, 'mas traz o que mudou no servidor')
    assert.equal(t.alvo.value.temMudancaNaoSalva(), true)
    assert.equal(t.mudou.length, 0, 'recarregar não avisa o pai (sem laço)')
    // PATCH barrado por conferência: relê e volta ao salvo
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: () => ({ __erro: { code: 'estoque_conferindo', message: 'em conferência' } }) })
    rotas.push({ metodo: 'GET', url: '/api/devolutions/L1', resposta: () => linha({ condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'conferindo', observacao: 'do servidor' }) })
    await t.digitar(t.campo(t.cartoes()[0], 'Observação'), 'x')
    await t.clicar(t.botao(t.cartoes()[0], 'salvar'))
    assert.equal(t.campo(t.cartoes()[0], 'Observação').value, 'do servidor')
    assert.match(t.cartoes()[0].textContent, /em conferência/)
    // o relógio dispara: entrada confirmada
    rotas = rotas.filter((r) => !(r.metodo === 'GET' && r.url === '/api/devolutions/L1'))
    rotas.push({ metodo: 'GET', url: '/api/devolutions/L1', resposta: () => linha({ condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'ok', estoque_mov_sku: 'f105.ci', estoque_mov_qty: 1 }) })
    const r = vivos()[0]; r.vivo = false; await r.fn(); await tick()
    assert.ok(titulos().includes('Bling · entrada confirmada'))
    assert.match(t.cartoes()[0].textContent, /estoque ok/)
    assert.equal(t.mudou.length, 1, 'estado do estoque mudou sozinho: avisa o pai')
    assert.equal(vivos().length, 0, 'nada mais a conferir: relógio parado')
    t.fim()
  }

  // 12 ─ Trocado (dois modais), Esc cancela só o modal, anexar/remover foto, rascunho "já voltou do técnico"
  {
    const t = await montar({ linhas: [linha()] })
    let [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Trocado')
    modalResposta.DevolucaoTrocaSkuModal = { sku: 'dg020.ci', condicao: 'Usado' }
    modalResposta.DevolucaoEstoqueModal = { suffix: 'us' }
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => linha({ ...body }) })
    await t.clicar(t.botao(c, 'salvar'))
    const [b] = corpo('PATCH', /L1$/)
    assert.equal(b.troca_sku, 'dg020.ci'); assert.equal(b.troca_condicao, 'Usado'); assert.equal(b.estoque_suffix, 'us'); assert.equal(b.devolver_estoque, true)
    assert.deepEqual(modalResposta.abertos.map((m) => `${m.nome}:${m.sku}:${m.condicao || ''}`), ['DevolucaoTrocaSkuModal:f105.ci:', 'DevolucaoEstoqueModal:dg020.ci:Usado'])
    assert.ok(titulos().includes('Lançamento salvo'))
    // anexar e remover: quem manda é o bloco Provas da janela (métodos expostos)
    c = t.cartoes()[0]
    rotas.push({ metodo: 'POST', url: '/api/devolutions/L1/anexos', resposta: () => linha({ condicao_produto: 'Trocado', anexos: [{ id: 'A1', filename: 'v.mp4', content_type: 'video/mp4', size_bytes: 1, ml_file_name: null, created_at: '2026-11-16T11:00:01Z' }] }) })
    await t.digitar(t.campo(c, 'Observação'), 'no meio')
    const antesDeAnexar = t.mudou.length
    assert.equal(await t.alvo.value.anexarFotos([new window.File(['x'], 'v.mp4', { type: 'video/mp4' })]), true, 'com lançamento: sobe na hora')
    await tick()
    assert.deepEqual(corpo('POST', /L1\/anexos$/), [{ __arquivo: 'v.mp4' }])
    assert.equal(t.mudou.length, antesDeAnexar + 1, 'avisa o pai (Provas relê as fotos)')
    c = t.cartoes()[0]
    assert.equal(t.campo(c, 'Observação').value, 'no meio', 'foto anexada não apaga o que estava sendo digitado')
    rotas.push({ metodo: 'DELETE', url: '/api/devolutions/anexos/A1', resposta: () => null })
    await t.alvo.value.removerFoto('A1'); await tick()
    assert.equal(chamadas.filter((x) => x.metodo === 'DELETE' && /anexos\/A1$/.test(x.url)).length, 1)
    assert.ok(titulos().includes('Foto removida'))
    // link colado em Provas com lançamento salvo: grava só esse campo no lançamento
    rotas.push({ metodo: 'PATCH', url: '/api/devolutions/L1', resposta: ({ body }) => linha({ condicao_produto: 'Trocado', ...body }) })
    assert.equal(await t.alvo.value.gravarLink('link_abertura', 'https://x.y/abertura'), null)
    assert.deepEqual(corpo('PATCH', /L1$/).at(-1), { link_abertura: 'https://x.y/abertura' })
    assert.equal(t.campo(t.cartoes()[0], 'Observação').value, 'no meio', 'gravar o link não mexe no que está digitado no cartão')
    t.fim()

    // Esc com o modal aberto: cancela o modal e não chega ao window (a janela do pai não fecha)
    modalResposta = {}
    const e = await montar({ linhas: [linha()] })
    const duble = modalResposta
    let chegou = 0
    const ouvinte = () => { chegou += 1 }
    window.addEventListener('keydown', ouvinte)
    // modal que NÃO responde sozinho: deixa aberto
    const original = globalThis.queueMicrotask
    globalThis.queueMicrotask = () => {}
    await e.escolher(e.campo(e.cartoes()[0], 'Condição'), 'Usado')
    e.botao(e.cartoes()[0], 'salvar').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await tick()
    globalThis.queueMicrotask = original
    assert.equal(modalResposta.abertos.length, 1)
    assert.match(e.botao(e.cartoes()[0], 'salvando').textContent, /salvando…/)
    window.document.body.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await tick()
    assert.equal(chegou, 0, 'Esc não passa adiante com o modal aberto')
    assert.ok(e.botao(e.cartoes()[0], 'salvar'), 'modal cancelado: cartão solto, nada salvo')
    assert.equal(chamadas.filter((x) => x.metodo === 'PATCH').length, 0)
    window.document.body.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await tick()
    assert.equal(chegou, 1, 'sem modal aberto o Esc segue pro pai')
    window.removeEventListener('keydown', ouvinte)
    e.fim()

    // rascunho de Manutenção com "já voltou do técnico" (só pra quem pode) + Sim→Não devolve a condição
    email = 'joffer4@tuta.com'
    const m = await montar({ pecas: [peca({ sku: 'dg020.ci' })] })
    c = m.cartoes()[0]
    await m.escolher(m.campo(c, 'Condição'), 'Usado')
    await m.escolher(m.campo(c, 'Vai pra manutenção?'), 'sim')
    assert.match(m.campo(c, 'Condição').selectedOptions[0].textContent, /vai pro técnico/)
    assert.equal(m.campo(c, 'Técnico').disabled, false)
    await m.escolher(m.campo(c, 'Vai pra manutenção?'), 'nao')
    assert.equal(m.campo(c, 'Condição').value, 'Usado'); assert.equal(m.campo(c, 'Técnico').disabled, true)
    await m.escolher(m.campo(c, 'Vai pra manutenção?'), 'sim')
    await m.provas({ linkAbertura: 'https://x.y/a' })
    await m.escolher(m.campo(c, 'Técnico'), 'Shark')
    const caixa = c.querySelector('input[type=checkbox]')
    assert.ok(caixa, 'quem pode vê o "já voltou do técnico"')
    caixa.checked = true; caixa.dispatchEvent(new window.Event('change', { bubbles: true })); await tick()
    modalResposta.DevolucaoManutencaoModal = { tipo: 'Sucata' }
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'MN', ...body }) })
    await m.clicar(m.botao(c, 'lançar'))
    const [pm] = corpo('POST', /devolutions$/)
    assert.equal(pm.condicao_produto, 'Manutenção'); assert.equal(pm.devolver_estoque, true); assert.equal(pm.manutencao_destino, 'Sucata'); assert.equal(pm.reembolso, true); assert.equal(pm.tecnico, 'Shark')
    assert.deepEqual(modalResposta.abertos.map((x) => x.nome), ['DevolucaoManutencaoModal'], 'Sucata na manutenção: sem modal de destino')
    m.fim()
    email = 'alguem@tuta.com'
    // sem a permissão: Manutenção lança sem modal e sem estoque
    const n = await montar({ pecas: [peca({ sku: 'dg020.ci' })] })
    c = n.cartoes()[0]
    await n.escolher(n.campo(c, 'Vai pra manutenção?'), 'sim')
    assert.equal(c.querySelector('input[type=checkbox]'), null)
    await n.provas({ linkAbertura: 'https://x.y/a' })
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'MN2', ...body }) })
    await n.clicar(n.botao(c, 'lançar'))
    assert.equal(corpo('POST', /devolutions$/)[0].devolver_estoque, false); assert.equal(modalResposta.abertos, undefined)
    n.fim()
  }

  // 13 ─ reembolso do rascunho vale pela condição FINAL: "Vai pra manutenção? Sim"
  // por engano e de volta pro Não não manda reembolso de um produto que voltou Novo
  {
    const t = await montar({ pecas: [peca({ sku: 'f105.ci' })] })
    const [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Vai pra manutenção?'), 'sim')
    await t.escolher(t.campo(c, 'Vai pra manutenção?'), 'nao')
    await t.escolher(t.campo(c, 'Condição'), 'Novo')
    modalResposta.DevolucaoEstoqueModal = { destino_sku: 'f105.sa' }
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: ({ body }) => linha({ id: 'R1', ...body }) })
    await t.clicar(t.botao(c, 'lançar'))
    const [b] = corpo('POST', /devolutions$/)
    assert.equal(b.condicao_produto, 'Novo'); assert.equal(b.reembolso, false, 'engano no Sim/Não não deixa reembolso ligado')
    t.fim()
  }

  // 14 ─ mostrar / esconder (pedido com mais de uma peça): linha-resumo com a condição
  // na cor; abre sozinha só a 1ª peça que falta lançar; o que tem pendência não esconde
  {
    const visivel = (c) => c.querySelector('[data-corpo]').style.display !== 'none'
    // uma peça só: sempre aberto, sem botão
    const u = await montar({ pecas: [peca()] })
    assert.equal(u.el.querySelector('[data-mostrar]'), null)
    assert.equal(visivel(u.cartoes()[0]), true)
    u.fim()

    // relógio lançado Novo (estoque ok) + celular no técnico + duas peças por lançar
    const t = await montar({
      pecas: [peca({ sku: 'a003.pi', ja_devolvido: true }), peca({ sku: 'g1s.ra', ja_devolvido: true }), peca({ sku: 'x1' }), peca({ sku: 'x2' })],
      linhas: [
        linha({ id: 'R1', sku: 'a003.pi', condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'ok', estoque_mov_sku: 'a003.pi', estoque_mov_qty: 1 }),
        linha({ id: 'C1', sku: 'g1s.ra', condicao_produto: 'Manutenção', tecnico: 'Shark', prazo: '2026-11-20T12:00:00Z', link_abertura: 'https://x.y/a' }),
      ],
    })
    let cs = t.cartoes()
    assert.deepEqual(cs.map(visivel), [false, false, true, false], 'lançadas fechadas; abre a 1ª que falta lançar')
    assert.equal(cs[0].querySelector('[data-resumo-condicao]').textContent.trim(), 'Novo')
    assert.match(cs[0].textContent, /estoque ok · a003\.pi/)
    assert.match(cs[1].querySelector('[data-resumo-condicao]').textContent, /no técnico · Shark · até 20\/11/)
    assert.match(cs[3].textContent, /falta lançar/)
    // verde só no que terminou: o relógio que entrou no estoque sim; o celular com o técnico não
    assert.equal(cs[0].querySelector('[data-terminou]').getAttribute('data-terminou'), 'sim')
    assert.match(cs[0].querySelector('[data-terminou]').textContent, /lançada 16\/11/)
    assert.equal(cs[1].querySelector('[data-terminou]').getAttribute('data-terminou'), 'nao')
    assert.match(cs[1].querySelector('[data-terminou]').textContent, /desde 16\/11/)
    // mostrar abre; esconder fecha
    await t.clicar(cs[1].querySelector('[data-mostrar]'))
    assert.equal(visivel(t.cartoes()[1]), true)
    await t.clicar(t.cartoes()[1].querySelector('[data-mostrar]'))
    assert.equal(visivel(t.cartoes()[1]), false)
    // mudança não salva prende aberto (não dá pra esconder o que não foi salvo)
    await t.clicar(t.cartoes()[0].querySelector('[data-mostrar]'))
    await t.digitar(t.campo(t.cartoes()[0], 'Observação'), 'veio sem a caixa')
    assert.equal(t.cartoes()[0].querySelector('[data-mostrar]').disabled, true)
    assert.equal(visivel(t.cartoes()[0]), true)
    // rascunho mexido também não esconde
    await t.escolher(t.campo(t.cartoes()[3], 'Condição'), 'Novo')
    assert.equal(visivel(t.cartoes()[3]), true)
    assert.equal(t.cartoes()[3].querySelector('[data-mostrar]').disabled, true)
    t.fim()

    // estoque que não entrou: a peça fica aberta sozinha
    const e = await montar({
      pecas: [peca({ sku: 'a003.pi', ja_devolvido: true }), peca({ sku: 'x1', ja_devolvido: true })],
      linhas: [
        linha({ id: 'E1', sku: 'a003.pi', condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'falhou', estoque_erro: 'Bling 429' }),
        linha({ id: 'E2', sku: 'x1', condicao_produto: 'Sucata', link_abertura: 'https://x.y/a' }),
      ],
    })
    assert.deepEqual(e.cartoes().map(visivel), [true, false])
    assert.equal(e.cartoes()[1].querySelector('[data-resumo-condicao]').textContent.trim(), 'Perdimento')
    e.fim()
  }

  // 15 ─ correções da revisão cética
  {
    // (6) sair da Manutenção pelo "Não" sem escolher a condição: não salva vazio
    const m = await montar({ pecas: [peca({ ja_devolvido: true })], linhas: [linha({ condicao_produto: 'Manutenção', link_abertura: 'https://x.y/a', reembolso: true, tecnico: 'Shark' })] })
    let [c] = m.cartoes()
    await m.escolher(m.campo(c, 'Vai pra manutenção?'), 'nao')
    await m.digitar(m.campo(c, 'Custo manutenção'), '80')
    await m.clicar(m.botao(c, 'salvar'))
    assert.equal(corpo('PATCH', /devolutions\/L1$/).length, 0, 'condição vazia ao sair da manutenção não vai pro servidor')
    assert.match(m.cartoes()[0].textContent, /Escolha como a peça voltou do técnico/)
    assert.equal(m.alvo.value.ocupado(), false)
    m.fim()

    // (3) erro sem resposta no POST: relê; a linha foi criada → vira lançamento, sem 2º POST
    let criada = false
    const t = await montar({ pecas: [peca({ sku: 'b056.24' })] })
    rotas.length = 0
    rotas.push({ metodo: 'POST', url: '/api/devolutions', resposta: () => { criada = true; throw Object.assign(new Error('fetch failed'), {}) } })
    rotas.push({ metodo: 'GET', url: /^\/api\/devolutions\/order-lookup\?pedido=/, resposta: () => [peca({ sku: 'b056.24', ja_devolvido: criada })] })
    rotas.push({ metodo: 'GET', url: /^\/api\/devolutions\?search=/, resposta: () => ({ items: criada ? [linha({ id: 'Z1', sku: 'b056.24', condicao_produto: 'Extraviado', link_abertura: 'https://x.y/a' })] : [] }) })
    ;[c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Extraviado')
    await t.escolher(t.campo(c, 'Motivo'), 'Não recebido')
    await t.provas({ linkAbertura: 'https://x.y/a' })
    await t.clicar(t.botao(c, 'lançar'))
    assert.deepEqual(t.cartoes().map((x) => x.getAttribute('data-lancamento')), ['Z1'], 'a peça criada no servidor aparece lançada')
    assert.ok(titulos().includes('O lançamento entrou — não lance de novo'))
    assert.equal(corpo('POST', /devolutions$/).length, 1)
    t.fim()
  }

  // 16 ─ travas aprovadas em 16/11: peça que JÁ entrou no estoque não vira Perdimento /
  // Extraviado / manutenção por cima (o estoque não seria estornado, ou entraria 2×)
  {
    const t = await montar({ pecas: [peca({ ja_devolvido: true })], linhas: [linha({ condicao_produto: 'Novo', devolver_estoque: true, estoque_status: 'ok', estoque_mov_sku: 'f105.sa', estoque_mov_qty: 1 })] })
    const [c] = t.cartoes()
    await t.escolher(t.campo(c, 'Condição'), 'Sucata')
    assert.equal(t.campo(c, 'Condição').value, 'Novo', 'o seletor volta')
    assert.match(c.textContent, /já entrou no estoque \(f105\.sa\).*apague este lançamento na lixeira/)
    await t.escolher(t.campo(c, 'Vai pra manutenção?'), 'sim')
    assert.equal(t.campo(c, 'Vai pra manutenção?').value, 'nao')
    assert.match(c.textContent, /Pra mandar pra manutenção, apague este lançamento/)
    // Novo → Usado continua passando (o sistema refaz a entrada)
    await t.escolher(t.campo(c, 'Condição'), 'Usado')
    assert.equal(t.campo(c, 'Condição').value, 'Usado')
    assert.equal(chamadas.filter((x) => x.metodo === 'PATCH').length, 0)
    t.fim()
    // sem movimento registrado (lançamento antigo): não trava — a lixeira não teria o que estornar
    const v = await montar({ pecas: [peca({ ja_devolvido: true })], linhas: [linha({ condicao_produto: 'Novo', devolver_estoque: true })] })
    await v.escolher(v.campo(v.cartoes()[0], 'Condição'), 'Extraviado')
    assert.equal(v.campo(v.cartoes()[0], 'Condição').value, 'Extraviado')
    v.fim()
  }

  console.log('ok — DevolucaoLancar')
  process.exit(0)
})().catch((e) => { console.error(e); process.exit(1) })
