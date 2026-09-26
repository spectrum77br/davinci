// Run from apps/web: node tests/companies-proxy.cjs
// Empresas › painel do Proxy (26/09/2026). Eduardo: "colocarmos usuário e
// senha, um toogle bem organizado, aí quando mudarmos o ip corrige corretamente
// e salva e já deixa no ar". Travas:
//  - abrir lê GET /proxy e preenche (senha NUNCA vem no formulário);
//  - salvar manda PUT com: senha null = mantém a guardada, "" = apagar (só com a
//    caixinha, que descarta a digitada), texto = trocar; porta número; perfis
//    extras; a versão (rev) que o painel abriu;
//  - porta, usuário e senha: tudo ou nada; com eles, IP obrigatório;
//  - colar "ip:porta:usuario:senha" ou "socks5://u:s@ip:porta" no IP separa nos
//    campos — a senha nunca vai no campo do IP;
//  - trocar o IP mantendo usuário e senha pede confirmação;
//  - "61, 109 n12" vira ['61','109','12']; lixo é recusado sem ir ao servidor;
//  - empresa com proxy: clicar no IP abre o painel (admin) em vez de editar;
//  - um painel só (fora do v-for filtrado); resposta de uma empresa não cai no
//    painel de outra; tela trancada fecha.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { ref, reactive, computed, watch, nextTick } = requireWeb('vue')
const ts = requireWeb('typescript')
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/companies/index.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: 'proxy' }).errors, [])

const tpl = descriptor.template.content
assert.match(tpl, /v-if="isAdmin && !isEditingCell\(row, 'ip'\)"[\s\S]{0,900}@click\.stop="abrirProxy\(row\)"/, 'chave só para admin, sem abrir a edição do IP')
assert.match(tpl, /@click="cliqueNoIp\(row\)"/, 'clique no IP passa pela regra do proxy')
assert.match(tpl, /v-model="proxyForm\.usuario"/)
assert.match(tpl, /v-model="proxyForm\.senha"[\s\S]{0,200}autocomplete="new-password"/, 'senha sem autocompletar')
assert.match(tpl, /@click="salvarProxy\(\)"/)
// Um painel só, fora da linha da tabela: não pode depender de `row`.
const painel = tpl.slice(tpl.indexOf('<!-- Painel do proxy'), tpl.indexOf('</Teleport>', tpl.indexOf('<!-- Painel do proxy')))
assert.ok(painel.includes('v-if="proxyAberto"'), 'painel abre por proxyAberto')
assert.ok(!/\brow\b/.test(painel), 'painel não usa a linha do v-for')
const linhaDaTabela = tpl.slice(tpl.indexOf('<tr v-for="row in filteredRows"'), tpl.indexOf('</table>'))
assert.ok(!linhaDaTabela.includes('Painel do proxy'), 'painel fora do v-for filtrado')
assert.ok(!/placeholder="\d+\.\d+\.\d+\.\d+"/.test(painel), 'placeholder do IP não é um IP de verdade')

const script = descriptor.scriptSetup.content
assert.match(script, /if \(!agora && antes\) \{\s*fecharCertificado\(\)\s*fecharProxy\(\)/, 'tela trancou: painel do proxy fecha')
const bloco = (de, ate) => {
  const i = script.indexOf(de)
  const j = script.indexOf(ate, i)
  assert.ok(i >= 0 && j > i, `bloco ${de}`)
  return script.slice(i, j)
}
const js = ts.transpileModule(
  bloco('// Traduz os erros que a pessoa', '// ---------- IP (um por empresa)') + '\n' +
  bloco('// ---------- proxy da empresa ----------', '// ---------- certificado digital ----------'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

const DADOS = {
  ip: '198.51.100.7', tipo: 'socks5', porta: 7128, usuario: 'velho', tem_senha: true, perfis_extras: ['61', '999'],
  perfis: [
    { profile_no: '34', nome: 'Barbosa - ML', origem: 'loja', compartilhado: false },
    { profile_no: '61', nome: 'Barbosa - ML shop', origem: 'extra', compartilhado: false },
  ],
  sem_perfil: ['perfil extra 999 não existe no AdsPower'], ip_adspower: '198.51.100.7', ip_adspower_em: null, ip_adspower_erro: null, rev: 2,
}
const copia = (x) => JSON.parse(JSON.stringify(x))

// Resposta que nunca chega (para testar corrida) = `pendente: true`.
function montar({ responder, confirmar = () => true, admin = true, podeEditar = true } = {}) {
  const chamadas = []
  let refreshes = 0
  let relogios = 0
  const perguntas = []
  const apiE = async (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
    await new Promise((r) => setImmediate(r))
    const r = responder ? responder(url, opts) : null
    if (r && r.pendente) return new Promise(() => {})
    if (r && r.erro) throw r.erro
    if (r) return r.valor
    if (url.endsWith('/proxy/senha')) return { senha: 'guardada-123' }
    return copia(opts.method === 'PUT' ? { ...DADOS, ip: opts.body.ip, ip_adspower: null, rev: 3 } : DADOS)
  }
  const grid = ref({ rows: [] })
  const error = ref(null)
  const editando = []
  const situacaoAdspower = (c) => (c.ip ? { simbolo: c.ip === c.ip_adspower ? '✓' : '⏳', classe: '', texto: c.ip_adspower_erro || '' } : null)
  const f = new Function(
    'ref', 'reactive', 'computed', 'watch', 'apiE', 'refresh', 'situacaoAdspower', 'ligarRelogio', 'confirm', 'grid', 'error',
    'isAdmin', 'canEdit', 'isEditingCell', 'startEditCell',
    '"use strict";\n' + js + `
return { proxyAberto, proxyDados, proxyForm, proxyExtras, proxyNovoExtra, proxyApagarSenha, proxySenhaVista, proxyErro, proxyAviso, proxyLinha,
  abrirProxy, fecharProxy, adicionarExtras, removerExtra, verSenhaProxy, salvarProxy, situacaoDoPainel, chipDoExtra, cliqueNoIp, separarLinhaDoProxy, soOIp }`)
  const pg = f(ref, reactive, computed, watch, apiE, async () => { refreshes++ }, situacaoAdspower, () => { relogios++ },
    (m) => { perguntas.push(m); return confirmar(m) }, grid, error,
    ref(admin), ref(podeEditar), () => false, (row, campo) => editando.push([row.company.id, campo]))
  const de = (m) => chamadas.filter((c) => c.method === m)
  const esperar = async () => { for (let i = 0; i < 10; i++) { await new Promise((r) => setImmediate(r)); await nextTick() } }
  return { pg, chamadas, de, esperar, grid, error, editando, perguntas, refreshes: () => refreshes, relogios: () => relogios }
}
const linha = (id, extra = {}) => ({ company: { id, apelido: 'Barbosa', ...extra }, stores: {} })
async function aberto(opcoes) {
  const t = montar(opcoes)
  await t.pg.abrirProxy(linha('e1'))
  await t.esperar()
  return t
}

;(async () => {
  // 1) abrir preenche; senha nunca vem no formulário
  {
    const t = await aberto()
    assert.equal(t.chamadas[0].url, '/api/companies/e1/proxy')
    assert.equal(t.pg.proxyForm.ip, '198.51.100.7')
    assert.equal(t.pg.proxyForm.porta, '7128')
    assert.equal(t.pg.proxyForm.usuario, 'velho')
    assert.equal(t.pg.proxyForm.senha, '')
    assert.deepEqual(t.pg.proxyExtras.value, ['61', '999'])
  }

  // 2) salvar: IP novo, usuário novo, senha nova, extras (+ o digitado e não
  //    acrescentado), rev; relógio liga porque o IP está a caminho
  {
    const t = await aberto()
    Object.assign(t.pg.proxyForm, { ip: '203.0.113.10', usuario: 'pxteste01', senha: 'nova-senha' })
    await t.esperar()
    t.pg.proxyNovoExtra.value = '109'
    await t.pg.salvarProxy()
    const [put] = t.de('PUT')
    assert.equal(put.url, '/api/companies/e1/proxy')
    assert.deepEqual(put.body, {
      ip: '203.0.113.10', tipo: 'socks5', porta: 7128, usuario: 'pxteste01', senha: 'nova-senha',
      perfis_extras: ['61', '999', '109'], rev: 2,
    })
    assert.equal(t.perguntas.length, 0, 'usuário e senha novos: não pergunta nada')
    assert.equal(t.refreshes(), 1)
    assert.equal(t.relogios(), 1, 'relógio liga para o ⏳ virar ✓ sozinho')
    assert.match(t.pg.proxyAviso.value, /até 1 minuto/)
    assert.equal(t.pg.proxyForm.senha, '', 'senha digitada sai do formulário depois de salvar')
  }

  // 3) senha em branco = mantém (null); caixinha = apagar ("") e descarta a
  //    digitada; apagar com usuário e porta ainda preenchidos não salva
  {
    const t = await aberto()
    await t.pg.salvarProxy()
    assert.equal(t.de('PUT')[0].body.senha, null)
    t.pg.proxyForm.senha = 'digitada'
    t.pg.proxyApagarSenha.value = true
    await t.esperar()
    assert.equal(t.pg.proxyForm.senha, '', 'caixinha descarta a senha digitada')
    await t.pg.salvarProxy()
    assert.equal(t.de('PUT').length, 1, 'apagar a senha e deixar usuário/porta = meio proxy')
    assert.match(t.pg.proxyErro.value, /porta, usuário e senha juntos/)
    Object.assign(t.pg.proxyForm, { usuario: '', porta: '' })
    await t.pg.salvarProxy()
    const put = t.de('PUT')[1]
    assert.equal(put.body.senha, '')
    assert.equal(put.body.usuario, null)
    assert.equal(put.body.porta, null)
  }

  // 4) tudo ou nada; com proxy, IP obrigatório; porta inválida
  {
    const t = await aberto({ responder: (url, o) => (o.method !== 'PUT' ? { valor: { ...copia(DADOS), tem_senha: false, usuario: null, porta: null } } : null) })
    t.pg.proxyForm.usuario = 'px1'
    await t.pg.salvarProxy()
    assert.equal(t.de('PUT').length, 0)
    assert.match(t.pg.proxyErro.value, /porta, usuário e senha juntos/)
    t.pg.proxyForm.porta = '7128'
    await t.pg.salvarProxy()
    assert.equal(t.de('PUT').length, 0, 'ainda sem senha')
    t.pg.proxyForm.senha = 'x'
    t.pg.proxyForm.ip = ''
    await t.esperar()
    await t.pg.salvarProxy()
    assert.match(t.pg.proxyErro.value, /Coloque o IP do proxy/)
    t.pg.proxyForm.ip = '203.0.113.10'
    t.pg.proxyForm.porta = '99999'
    await t.esperar()
    await t.pg.salvarProxy()
    assert.match(t.pg.proxyErro.value, /Porta inválida/)
    assert.equal(t.de('PUT').length, 0)
  }

  // 5) colar a linha do proxy no IP: separa nos campos, senha no campo de senha
  {
    const t = await aberto()
    t.pg.proxyForm.ip = ' 203.0.113.10:7129:pxteste01:Se:nha9 '
    await t.esperar()
    assert.equal(t.pg.proxyForm.ip, '203.0.113.10')
    assert.equal(t.pg.proxyForm.porta, '7129')
    assert.equal(t.pg.proxyForm.usuario, 'pxteste01')
    assert.equal(t.pg.proxyForm.senha, 'Se:nha9', 'senha com ":" fica inteira')
    assert.match(t.pg.proxyAviso.value, /separada nos campos/)
    t.pg.proxyForm.ip = 'http://outro:S3nh@@203.0.113.11:8080'
    await t.esperar()
    assert.deepEqual([t.pg.proxyForm.ip, t.pg.proxyForm.porta, t.pg.proxyForm.usuario, t.pg.proxyForm.senha, t.pg.proxyForm.tipo],
      ['203.0.113.11', '8080', 'outro', 'S3nh@', 'http'])
    await t.pg.salvarProxy()
    const put = t.de('PUT')[0]
    assert.equal(put.body.ip, '203.0.113.11')
    assert.ok(!JSON.stringify(put.body.ip).includes('S3nh'), 'senha nunca vai no campo IP')
    // IP com porta só: não mexe no usuário
    const u = t.pg.separarLinhaDoProxy('203.0.113.12:7128')
    assert.deepEqual(u, { ip: '203.0.113.12', porta: '7128', usuario: null, senha: null, tipo: null })
    assert.equal(t.pg.separarLinhaDoProxy('lixo:1:2'), null)
  }

  // 6) trocar só o IP e manter usuário/senha pede confirmação; "não" não salva
  {
    const t = await aberto({ confirmar: () => false })
    t.pg.proxyForm.ip = '203.0.113.10'
    await t.esperar()
    await t.pg.salvarProxy()
    assert.equal(t.perguntas.length, 1)
    assert.match(t.perguntas[0], /mesmo usuário e senha/)
    assert.equal(t.de('PUT').length, 0)
    const t2 = await aberto({ confirmar: () => true })
    t2.pg.proxyForm.ip = '203.0.113.10'
    await t2.esperar()
    await t2.pg.salvarProxy()
    assert.equal(t2.de('PUT').length, 1, 'confirmou: salva')
  }

  // 7) perfis extras: vários de uma vez, sem repetir; lixo é recusado; chips
  {
    const t = await aberto()
    t.pg.proxyNovoExtra.value = '61, 109 n12;#7'
    t.pg.adicionarExtras()
    assert.deepEqual(t.pg.proxyExtras.value, ['61', '999', '109', '12', '7'])
    t.pg.removerExtra('12')
    assert.deepEqual(t.pg.proxyExtras.value, ['61', '999', '109', '7'])
    assert.equal(t.pg.chipDoExtra('61').titulo, 'Barbosa - ML shop')
    assert.equal(t.pg.chipDoExtra('999').marca, ' ⚠', 'número que não existe no AdsPower')
    assert.equal(t.pg.chipDoExtra('109').marca, ' (novo)')
    t.pg.proxyDados.value.perfis[1].compartilhado = true
    assert.match(t.pg.chipDoExtra('61').titulo, /NÃO troca/)
    t.pg.proxyNovoExtra.value = '34; abc'
    t.pg.adicionarExtras()
    assert.match(t.pg.proxyErro.value, /inválido/)
    await t.pg.salvarProxy()
    assert.equal(t.de('PUT').length, 0, 'extra inválido não vai ao servidor')
  }

  // 8) erros do servidor traduzidos
  {
    const erro = (detail) => {
      const e = new Error('409')
      Object.defineProperty(e, 'data', { get: () => ({ detail }) })
      return e
    }
    const casos = [
      [{ code: 'ip_exists', empresa: 'Inova' }, /já é da empresa Inova/],
      [{ code: 'perfil_de_outra_empresa', perfil: '61', empresa: 'Inova' }, /perfil 61 já é da empresa Inova/],
      [{ code: 'proxy_mudou_enquanto_editava' }, /Feche e abra de novo/],
      [{ code: 'proxy_incompleto' }, /juntos/],
    ]
    for (const [detail, re] of casos) {
      const t = await aberto({ responder: (url, o) => (o.method === 'PUT' ? { erro: erro(detail) } : null) })
      await t.pg.salvarProxy()
      assert.match(t.pg.proxyErro.value, re)
    }
  }

  // 9) resposta de uma empresa não cai no painel de outra; senha idem
  {
    const t = montar({ responder: (url) => (url.includes('/e1/') ? { pendente: true } : null) })
    t.pg.abrirProxy(linha('e1'))
    await t.pg.abrirProxy(linha('e2'))
    await t.esperar()
    assert.equal(t.pg.proxyAberto.value, 'e2')
    assert.equal(t.pg.proxyForm.usuario, 'velho')
    const t2 = montar({ responder: (url) => (url.endsWith('/senha') ? { pendente: true } : null) })
    await t2.pg.abrirProxy(linha('e1'))
    await t2.esperar()
    t2.pg.verSenhaProxy()
    t2.pg.fecharProxy()
    await t2.esperar()
    assert.equal(t2.pg.proxySenhaVista.value, null)
    assert.equal(t2.pg.proxyDados.value, null)
  }

  // 10) situação no topo acompanha a tabela recarregada (⏳ vira ✓)
  {
    const t = await aberto({ responder: (url, o) => (o.method !== 'PUT' ? { valor: { ...copia(DADOS), ip_adspower: null } } : null) })
    t.grid.value = { rows: [linha('e1', { ip: '198.51.100.7', ip_adspower: null })] }
    assert.equal(t.pg.situacaoDoPainel().simbolo, '⏳')
    t.grid.value = { rows: [linha('e1', { ip: '198.51.100.7', ip_adspower: '198.51.100.7' })] }
    assert.equal(t.pg.situacaoDoPainel().simbolo, '✓')
    assert.equal(t.pg.proxyLinha.value.company.apelido, 'Barbosa')
  }

  // 11) clique no IP: com proxy, admin abre o painel; quem não é admin recebe
  //     o aviso; sem proxy, edita na célula como antes
  {
    const t = montar()
    t.pg.cliqueNoIp(linha('e1', { proxy_configurado: true }))
    await t.esperar()
    assert.equal(t.pg.proxyAberto.value, 'e1')
    assert.deepEqual(t.editando, [])
    const u = montar({ admin: false })
    u.pg.cliqueNoIp(linha('e1', { proxy_configurado: true }))
    assert.equal(u.pg.proxyAberto.value, null)
    assert.match(u.error.value, /painel do proxy/)
    u.pg.cliqueNoIp(linha('e2', { proxy_configurado: false }))
    assert.deepEqual(u.editando, [['e2', 'ip']])
  }

  console.log('PASS: painel do proxy — abre sem senha, salva com rev, tudo-ou-nada, colar a linha separa, confirma troca só de IP, chips com aviso, erros traduzidos, sem vazar entre empresas, situação ao vivo, clique no IP')
  process.exit(0)
})().catch((e) => { console.error(e); process.exit(1) })
