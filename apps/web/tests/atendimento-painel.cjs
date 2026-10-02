// node tests/atendimento-painel.cjs — painel do pedido, nota interna, foto e
// AdsPower na caixa /atendimento (item 3 do Comunicador, 01/10/2026).
// Testa os ajudantes exportados pelo <script> comum (não o setup) de cada
// componente: o botão do AdsPower (endereço, leitura da resposta, o caminho
// com e sem CORS), a tradução do bloqueio do ML, a nota interna e o saldo.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

function modulo(nome) {
  const filename = path.resolve(__dirname, `../components/${nome}`)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], nome)
  assert.ok(descriptor.script, `${nome} sem <script> comum`)
  assert.ok(descriptor.scriptSetup, `${nome} sem <script setup>`)
  const js = ts.transpileModule(descriptor.script.content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', js)((n) => require(n), mod, mod.exports)
  return mod.exports
}

async function main() {
  // ─── AdsPower ─────────────────────────────────────────────────────────────
  const ads = modulo('AtendimentoAdsPower.vue')
  const {
    ADSPOWER_API,
    MENSAGENS_ADSPOWER,
    abrirNoAdsPower,
    abrirPaginaNoPerfil,
    destinoPermitido,
    urlIniciarNaPagina,
    classificarRespostaAdsPower,
    urlIniciar,
  } = ads

  // 127.0.0.1 literal (não local.adspower.net: conteúdo misto).
  assert.equal(ADSPOWER_API, 'http://127.0.0.1:50325')
  assert.equal(urlIniciar({ perfil: '84' }), 'http://127.0.0.1:50325/api/v1/browser/start?serial_number=84')
  assert.equal(urlIniciar({ perfil: ' 84 ' }), 'http://127.0.0.1:50325/api/v1/browser/start?serial_number=84')
  // Sem nº, o id do perfil (robô do Mac mini); nada que mexa na URL.
  assert.equal(urlIniciar({ perfil: null, perfil_id: 'k1do5vfw' }), 'http://127.0.0.1:50325/api/v1/browser/start?user_id=k1do5vfw')
  assert.equal(urlIniciar({ perfil: '84&user_id=x' }), null)
  assert.equal(urlIniciar({ perfil: null, perfil_id: 'a b' }), null)
  assert.equal(urlIniciar(null), null)

  // A resposta do AdsPower → a frase do RF11.
  assert.equal(classificarRespostaAdsPower({ code: 0, msg: 'success' }).status, 'aberto')
  assert.equal(classificarRespostaAdsPower({ code: '0' }).status, 'aberto')
  assert.equal(classificarRespostaAdsPower({ code: '' }).status, 'erro')
  assert.equal(classificarRespostaAdsPower({ code: -1, msg: 'Profile does not exist' }).codigo, 'perfil_nao_encontrado')
  assert.match(MENSAGENS_ADSPOWER.perfil_nao_encontrado.detalhe, /tente de novo/)
  assert.equal(classificarRespostaAdsPower({ code: -1, msg: 'Too many request per second, please check' }).codigo, 'limite')
  assert.equal(classificarRespostaAdsPower({ code: -1, msg: 'The profile is being used by another device' }).codigo, 'perfil_em_uso')
  assert.match(MENSAGENS_ADSPOWER.perfil_em_uso.detalhe, /robô/)
  assert.equal(classificarRespostaAdsPower({ code: -1, msg: 'api-key is invalid' }).codigo, 'chave')
  const outro = classificarRespostaAdsPower({ code: -1, msg: 'algo novo' })
  assert.equal(outro.codigo, 'desconhecido')
  assert.match(outro.detalhe, /algo novo/)
  assert.equal(classificarRespostaAdsPower(null).status, 'erro')
  assert.equal(MENSAGENS_ADSPOWER.adspower_fechado.titulo, 'AdsPower não está aberto neste computador')
  assert.equal(MENSAGENS_ADSPOWER.sem_perfil.titulo, 'Esta loja não tem perfil do AdsPower cadastrado')

  // Só página LOCAL lê a resposta: o AdsPower recusa (403 "CORS ERROR") pedido
  // com Origin de outro site — medido no Mac mini em 01/10/2026.
  assert.equal(ads.podeLerResposta('http://localhost:3000'), true)
  assert.equal(ads.podeLerResposta('http://127.0.0.1:3000'), true)
  assert.equal(ads.podeLerResposta('https://app.hadken.com'), false)
  assert.equal(ads.podeLerResposta(undefined), false)

  const url = urlIniciar({ perfil: '84' })
  const semEspera = { esperar: async () => {} }
  const lendo = { ...semEspera, lerResposta: true }

  // 0. Produção (página https de fora): direto sem ler — o pedido vai SEM
  //    Origin (no-cors + GET), que é o que o AdsPower aceita.
  {
    const feitos = []
    const fetch = async (u, init) => {
      feitos.push([u, init.mode])
      return { ok: false, status: 0, type: 'opaque' }
    }
    const r = await abrirNoAdsPower(url, { ...semEspera, fetch })
    assert.equal(r.status, 'enviado')
    assert.deepEqual(feitos, [
      ['http://127.0.0.1:50325/status', 'no-cors'],
      [url, 'no-cors'],
    ])
  }

  // 1. CORS liberado: lê a resposta do start.
  {
    const feitos = []
    const fetch = async (u, init) => {
      feitos.push([u, init.mode])
      if (u.endsWith('/status')) return { ok: true, status: 200, json: async () => ({ code: 0 }) }
      return { ok: true, status: 200, json: async () => ({ code: 0, data: { ws: {} } }) }
    }
    const r = await abrirNoAdsPower(url, { ...lendo, fetch })
    assert.equal(r.status, 'aberto')
    assert.deepEqual(feitos, [
      ['http://127.0.0.1:50325/status', 'cors'],
      [url, 'cors'],
    ])
  }
  // ... e o erro do AdsPower aparece.
  {
    const fetch = async (u) => (u.endsWith('/status')
      ? { ok: true, status: 200, json: async () => ({ code: 0 }) }
      : { ok: true, status: 200, json: async () => ({ code: -1, msg: 'Profile does not exist' }) })
    const r = await abrirNoAdsPower(url, { ...lendo, fetch })
    assert.deepEqual([r.status, r.codigo], ['erro', 'perfil_nao_encontrado'])
  }
  // 2. CORS bloqueado (o fetch cors falha), mas o AdsPower está lá: manda sem ler.
  {
    const feitos = []
    const fetch = async (u, init) => {
      feitos.push([u, init.mode])
      if (init.mode === 'cors') throw new TypeError('Failed to fetch')
      return { ok: false, status: 0, type: 'opaque' }
    }
    const r = await abrirNoAdsPower(url, { ...lendo, fetch })
    assert.equal(r.status, 'enviado')
    assert.match(r.detalhe, /abrir numa aba/)
    assert.deepEqual(feitos.map((f) => f[1]), ['cors', 'no-cors', 'no-cors'])
    assert.equal(feitos[2][0], url)
  }
  // 3. Nada responde: AdsPower fechado (ou a rede local bloqueada) — e o start
  //    nem é chamado.
  {
    const feitos = []
    const fetch = async (u) => {
      feitos.push(u)
      throw new TypeError('Failed to fetch')
    }
    const r = await abrirNoAdsPower(url, { ...semEspera, fetch })
    assert.deepEqual([r.status, r.codigo], ['erro', 'adspower_fechado'])
    assert.match(r.detalhe, /rede local/)
    assert.ok(feitos.every((u) => u.endsWith('/status')))
  }
  // 4. Pendurou: o prazo corta (sem travar a tela).
  {
    const fetch = (u, init) => new Promise((_, rej) => init.signal.addEventListener('abort', () => rej(new Error('abort'))))
    const r = await abrirNoAdsPower(url, { ...semEspera, fetch, prazoMs: 20, prazoStartMs: 20 })
    assert.equal(r.codigo, 'adspower_fechado')
  }
  // Abrir já na página da plataforma (perfil fechado): launch_args com o link,
  // só https de domínio de plataforma (02/10/2026, medido com o perfil 72).
  {
    const p = { perfil: '72' }
    const u = urlIniciarNaPagina(p, 'https://www.mercadolivre.com.br/vendas/2000018464125672/detalhe')
    assert.ok(u.startsWith('http://127.0.0.1:50325/api/v1/browser/start?serial_number=72&launch_args='))
    assert.deepEqual(JSON.parse(decodeURIComponent(u.split('launch_args=')[1])), ['https://www.mercadolivre.com.br/vendas/2000018464125672/detalhe'])
    // Shopee: a página do pedido (order_id interno, só dígitos) e as buscas
    // pelo order_sn / return_sn — a query string chega inteira ao perfil.
    assert.equal(destinoPermitido('https://seller.shopee.com.br/portal/sale/order/244141571124463'), 'https://seller.shopee.com.br/portal/sale/order/244141571124463')
    assert.equal(destinoPermitido('https://seller.shopee.com.br/portal/sale/order?search=26092743U4QU7F'), 'https://seller.shopee.com.br/portal/sale/order?search=26092743U4QU7F')
    const busca = 'https://seller.shopee.com.br/portal/sale/returnrefundcancel?keyword=2609280ABCDEFGH&keywordType=return_sn'
    assert.equal(destinoPermitido(busca), busca)
    assert.deepEqual(JSON.parse(decodeURIComponent(urlIniciarNaPagina(p, busca).split('launch_args=')[1])), [busca])
    assert.ok(destinoPermitido('https://seller-br.tiktok.com/order/detail?order_no=1'))
    assert.equal(destinoPermitido('http://www.mercadolivre.com.br/x'), null) // só https
    assert.equal(destinoPermitido('https://mercadolivre.com.br.golpe.com/x'), null) // domínio de fora
    assert.equal(destinoPermitido('javascript:alert(1)'), null)
    // Destino recusado: abre só o perfil (sem launch_args).
    assert.equal(urlIniciarNaPagina(p, 'https://golpe.com/x'), 'http://127.0.0.1:50325/api/v1/browser/start?serial_number=72')
  }
  // O clique "Abrir no ML" pelo perfil: copia o link e manda o start com ele.
  {
    const feitos = []
    const copias = []
    const fetch = async (u) => { feitos.push(u); return { ok: false, status: 0, type: 'opaque' } }
    const r = await abrirPaginaNoPerfil({ perfil: '72' }, 'https://www.mercadolivre.com.br/vendas/1/detalhe', {
      ...semEspera, fetch, copiar: async (x) => { copias.push(x) },
    })
    assert.equal(r.status, 'enviado')
    assert.equal(r.copiado, true)
    assert.deepEqual(copias, ['https://www.mercadolivre.com.br/vendas/1/detalhe'])
    assert.ok(feitos.some((u) => u.includes('launch_args=')))
    assert.equal(await abrirPaginaNoPerfil({ perfil: null }, 'https://www.mercadolivre.com.br/x', { ...semEspera, fetch }), null)
  }
  // 5. A sondagem respondeu e o start demorou (o perfil leva mais de 20 s para
  //    subir): é "enviado", nunca "não está aberto" (02/10/2026).
  {
    const fetch = (u, init) => (u.endsWith('/status')
      ? Promise.resolve({ ok: false, status: 0, type: 'opaque' })
      : new Promise((_, rej) => init.signal.addEventListener('abort', () => rej(new Error('abort')))))
    const r = await abrirNoAdsPower(url, { ...semEspera, fetch, prazoMs: 20, prazoStartMs: 20 })
    assert.equal(r.status, 'enviado')
    assert.equal(r.codigo, null)
  }
  // O intervalo entre a sondagem e o start respeita o limite do AdsPower.
  {
    const esperas = []
    const fetch = async () => ({ ok: true, status: 200, json: async () => ({ code: 0 }) })
    await abrirNoAdsPower(url, { fetch, lerResposta: true, esperar: async (ms) => { esperas.push(ms) } })
    assert.deepEqual(esperas, [ads.INTERVALO_ADSPOWER_MS])
    assert.ok(ads.INTERVALO_ADSPOWER_MS >= 1000)
    assert.ok(ads.TRAVA_CLIQUE_MS >= 2000)
  }

  // ─── bloqueio do ML em português ─────────────────────────────────────────
  const { bloqueioLegivel, MOTIVOS_BLOQUEIO } = modulo('AtendimentoConversa.vue')
  for (const visto of ['blocked_by_cancelled_order', 'blocked_by_claim', 'blocked_by_mediation', 'blocked_by_conversation_initiated_by_seller_limited']) {
    assert.ok(MOTIVOS_BLOQUEIO[visto], visto)
    assert.doesNotMatch(bloqueioLegivel(visto), /blocked_by/, visto)
  }
  assert.match(bloqueioLegivel('blocked_by_cancelled_order'), /cancelado/)
  assert.match(bloqueioLegivel('BLOCKED_BY_MEDIATION'), /mediação/)
  assert.equal(bloqueioLegivel('blocked_by_algo_novo'), 'a plataforma bloqueou a conversa (blocked_by_algo_novo)')
  // Frase do backend passa como veio; vazio vira vazio.
  assert.equal(bloqueioLegivel('A janela de resposta da plataforma já fechou.'), 'A janela de resposta da plataforma já fechou.')
  assert.equal(bloqueioLegivel(null), '')
  assert.match(bloqueioLegivel('DELETED'), /apagada/)

  // ─── nota interna ────────────────────────────────────────────────────────
  const nota = modulo('AtendimentoNota.vue')
  assert.equal(nota.NOTA_MAX, 4000)
  assert.equal(nota.eNota({ tipo: 'nota', origem: 'davinci_nota' }), true)
  assert.equal(nota.eNota({ tipo: 'texto', origem: 'davinci_nota' }), true)
  assert.equal(nota.eNota({ tipo: 'nota', origem: 'x' }), true)
  assert.equal(nota.eNota({ tipo: 'texto', origem: 'davinci_humano' }), false)
  assert.equal(nota.eNota(null), false)

  // ─── painel: margem e saldo ──────────────────────────────────────────────
  const pedido = modulo('AtendimentoPedido.vue')
  assert.equal(pedido.pct(0.165), '16,5%')
  assert.equal(pedido.pct(-0.02), '-2,0%')
  assert.equal(pedido.pct(null), '—')
  assert.deepEqual(pedido.situacaoDoSaldo({ existe: true, saldo: 1, quantidade: 2, cobre: false }), { nivel: 'falta', texto: 'não cobre 2' })
  assert.deepEqual(pedido.situacaoDoSaldo({ existe: true, saldo: 5, quantidade: 2, cobre: true }), { nivel: 'cobre', texto: 'cobre 2' })
  assert.equal(pedido.situacaoDoSaldo({ existe: false, saldo: null, quantidade: 1, cobre: null }).nivel, 'sem_dado')
  assert.equal(pedido.situacaoDoSaldo({ existe: true, saldo: null, quantidade: 1, cobre: null }).texto, 'sem saldo lido')

  // ─── o template usa o que tem que usar ───────────────────────────────────
  const conversa = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
  assert.match(conversa, /<AtendimentoAdsPower/)
  assert.match(conversa, /<AtendimentoNota\s/)
  assert.match(conversa, /\/foto`/)
  // Os encaixes das frentes A e B foram preenchidos pelo integrador (o
  // cartão da reclamação, o selo da etiqueta) — conferidos em
  // tests/atendimento-comunicador.cjs.
  assert.match(conversa, /<AtendimentoReclamacao\s/)
  assert.match(conversa, /<AtendimentoEtiqueta\s/)
  const painel = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPedido.vue'), 'utf8')
  assert.match(painel, /ENCAIXE \(item 4/)

  console.log('atendimento-painel: ok')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
