// Run from apps/web: node tests/companies-certificado-excluir.cjs
// Cadastros › Empresas (25/09/2026), Eduardo: "no certificado digital só
// aparece o mês e o ano que vence, precisa aparecer o dia [...] um botão para
// excluir o certificado" e depois "precisa deixar para baixar, a senha quando
// colocarmos dentro é para travar e não deixarem baixar, e a opção de excluir
// senha, mas para excluir tem que colocar a senha atual". Travas:
//  - o selo mostra DD/MM/AAAA; no próprio dia do vencimento ainda "vence"
//    (amarelo), "venceu" só a partir do dia seguinte;
//  - o painel lista TODOS os certificados da empresa (renovação subida em dobro)
//    e cada um tem baixar / excluir senha / excluir;
//  - baixar certificado COM senha pede a senha (vai no corpo do POST); sem senha
//    baixa direto; erro do servidor (que chega como arquivo) vira mensagem clara;
//  - excluir senha e trocar senha mandam a senha atual; a tela não tem mais
//    como MOSTRAR a senha guardada;
//  - excluir: confirma, apaga AQUELE, sai da tela mesmo se a recarga falhar,
//    404 = já excluído, clique repetido não duplica;
//  - a resposta de uma empresa não aparece no painel de outra aberta depois.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { ref } = requireWeb('vue')
const ts = requireWeb('typescript')
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/companies/index.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'companies-cert' })
assert.deepEqual(template.errors, [])

// A ficha da empresa também tem certificados: mesma trava.
{
  const fichaFile = path.resolve(__dirname, '../pages/companies/[id].vue')
  const ficha = parse(fs.readFileSync(fichaFile, 'utf8'), { filename: fichaFile })
  assert.deepEqual(ficha.errors, [])
  assert.deepEqual(compileTemplate({ source: ficha.descriptor.template.content, filename: fichaFile, id: 'ficha' }).errors, [])
  const sc = ficha.descriptor.scriptSetup.content
  assert.doesNotMatch(sc, /certificates\/\$\{[^}]+\}\/password/, 'ficha não pede mais a senha guardada')
  assert.match(sc, /\/download`,\s*\{ method: 'POST', body: \{ password: senha \|\| null \}/, 'ficha baixa por POST com a senha')
  assert.match(sc, /body: \{ password: null, current_password: senhaAtual \}/, 'ficha exclui senha com a atual')
}

// --- peças exigidas do template
const tpl = descriptor.template.content
{
  assert.match(tpl, /v-for="\(c, i\) in certificadosDoPainel\(row\)"/, 'painel lista todos os certificados')
  assert.match(tpl, /@click="excluirCertificado\(row, c\)"/, 'cada certificado tem o seu excluir')
  assert.match(tpl, /@click="pedirAcaoCertificado\(row, c, 'baixar'\)"/, 'cada certificado tem o seu baixar')
  assert.match(tpl, /v-if="c\.has_password"[\s\S]{0,200}@click="pedirAcaoCertificado\(row, c, 'tirar_senha'\)"/, 'excluir senha só quando tem senha')
  assert.match(tpl, /:disabled="certOcupado\(\)"\s*@click="salvarCertificado\(row\)"/, 'salvar travado durante outra ação')
  assert.match(tpl, /v-model="certSenhaAtual"/, 'trocar senha pede a atual')
  assert.match(tpl, /max-h-48 overflow-y-auto/, 'lista longa rola dentro do painel')
  assert.doesNotMatch(tpl, /ver a senha guardada/, 'a senha guardada não aparece mais')
  assert.doesNotMatch(tpl, /troca de arquivo abaixo valem/, 'subir arquivo acrescenta, não troca')
}

// --- lógica: o bloco do certificado da página + as mensagens de erro, como estão
const script = descriptor.scriptSetup.content
assert.doesNotMatch(script, /certificates\/\$\{[^}]+\}\/password/, 'nenhuma chamada à rota que mostrava a senha')
const bloco = (de, ate) => {
  assert.ok(script.includes(de) && script.includes(ate), `marcadores: ${de}`)
  return script.slice(script.indexOf(de), script.indexOf(ate))
}
const trecho = [
  bloco('// Traduz os erros que a pessoa', '// Fica só com o IP'),
  bloco('// ---------- certificado digital ----------', '// ---------- inline obs edit ----------'),
].join('\n')
const js = ts.transpileModule(trecho, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

// Download: o navegador de mentira guarda o que seria salvo.
const baixados = []
globalThis.document = {
  createElement: () => ({ click() { baixados.push(this.download) }, remove() {} }),
  body: { appendChild() {} },
}

const erroApi = (code, comoArquivo = false) => {
  const corpo = { detail: { code } }
  return { data: comoArquivo ? new Blob([JSON.stringify(corpo)], { type: 'application/json' }) : corpo }
}

// lista: array fixo, ou função (url, n) com n = quantas GET de lista já houve
// responder(url, opts): devolve { erro } ou { valor } para chamadas que não são GET de lista
// gridDepois: o que o refresh() põe na tabela; undefined = refresh falha (grid fica como estava)
function montar({ lista = [], gridAntes = null, gridDepois, confirma = true, lifo = false, responder } = {}) {
  const chamadas = []
  let refreshes = 0
  let listas = 0
  let bloqueios = 0
  const grid = ref(gridAntes)
  const aguardando = []
  const apiE = (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET', body: opts.body, responseType: opts.responseType })
    return new Promise((resolve, reject) => {
      aguardando.push(() => {
        if ((opts.method || 'GET') === 'GET' && url.endsWith('/certificates')) {
          const r = typeof lista === 'function' ? lista(url, listas++) : lista
          return r && r.erro ? reject(erroApi(r.erro)) : resolve(r)
        }
        const r = responder ? responder(url, opts) : null
        if (r && r.erro) return reject(r.erro)
        if (opts.responseType === 'blob') return resolve(new Blob(['PFX']))
        resolve(r ? r.valor : null)
      })
    })
  }
  const refresh = async () => { refreshes++; if (gridDepois !== undefined) grid.value = gridDepois }
  const confirmar = []
  const confirm = (msg) => { confirmar.push(msg); return confirma }
  const trava = { eTravamento: (e) => e?.data?.detail?.code === 'empresas_locked' }
  const bloquear = () => { bloqueios++ }
  const factory = new Function(
    'ref', 'apiE', 'refresh', 'grid', 'confirm', 'trava', 'bloquear',
    js + `
return { certAberto, certLista, certExcluindo, certSalvando, certSenha, certSenhaAtual, certErro, certAcao, certSenhaAcao,
  certBaixandoId, alternarCertificado, certificadosDoPainel, excluirCertificado, salvarCertificado, pedirAcaoCertificado,
  confirmarAcaoCertificado, fecharAcaoCertificado, vencimentoCertificado, dataBR }`,
  )
  const pg = factory(ref, apiE, refresh, grid, confirm, trava, bloquear)
  // Resolve tudo o que está pendurado, inclusive o que for pedido no caminho.
  const soltar = async () => {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setImmediate(r))
      if (!aguardando.length) continue
      const lote = aguardando.splice(0)
      ;(lifo ? lote.reverse() : lote).forEach((f) => f())
    }
  }
  const de = (metodo) => chamadas.filter((c) => c.method === metodo)
  return {
    pg, grid, chamadas, confirmar, soltar, de,
    refreshes: () => refreshes, bloqueios: () => bloqueios,
    deletes: () => de('DELETE').map((c) => c.url),
  }
}

const CERT_A = { id: 'a', filename: 'JLAS.pfx', has_password: true, expires_at: '2027-07-27' }
const CERT_B = { id: 'b', filename: 'JLAS antigo.pfx', has_password: true, expires_at: '2026-10-01' }
const CERT_LIVRE = { id: 'l', filename: 'LIVRE.pfx', has_password: false, expires_at: null }
const linha = (id, cert, total = 2) => ({ company: { id, apelido: 'jlas' }, stores: {}, certificado: cert ? { ...cert, total } : null })
const isoDaqui = (d) => {
  const x = new Date()
  x.setDate(x.getDate() + d)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}
async function abrir(t, row) {
  t.pg.alternarCertificado(row)
  await t.soltar()
}

;(async () => {
  // 1) data completa no selo, com vencido / vencendo, contando dias de calendário
  {
    const { pg } = montar()
    assert.equal(pg.dataBR('2027-01-15'), '15/01/2027')
    assert.equal(pg.vencimentoCertificado({ expires_at: '2027-01-15' }).texto, '15/01/2027')
    assert.equal(pg.vencimentoCertificado({ expires_at: null }), null)
    assert.equal(pg.vencimentoCertificado({ expires_at: '2020-03-02' }).vencido, true)
    const hoje = pg.vencimentoCertificado({ expires_at: isoDaqui(0) })
    assert.deepEqual([hoje.vencido, hoje.vencendo], [false, true], 'no dia do vencimento ainda vence')
    const ontem = pg.vencimentoCertificado({ expires_at: isoDaqui(-1) })
    assert.deepEqual([ontem.vencido, ontem.vencendo], [true, false])
    const d30 = pg.vencimentoCertificado({ expires_at: isoDaqui(30) })
    assert.deepEqual([d30.vencido, d30.vencendo], [false, true])
    const d31 = pg.vencimentoCertificado({ expires_at: isoDaqui(31) })
    assert.deepEqual([d31.vencido, d31.vencendo], [false, false])
  }

  // 2) abrir o painel busca a lista; antes dela chegar, o mais novo já aparece
  {
    const t = montar({ lista: [CERT_A, CERT_B] })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    assert.deepEqual(t.pg.certificadosDoPainel(row).map((c) => c.id), ['a'])
    await t.soltar()
    assert.deepEqual(t.pg.certificadosDoPainel(row).map((c) => c.id), ['a', 'b'])
    assert.equal(t.chamadas[0].url, '/api/companies/e1/certificates')
  }

  // 3) empresa sem certificado: não busca lista
  {
    const t = montar()
    t.pg.alternarCertificado(linha('e1', null))
    assert.equal(t.chamadas.length, 0)
  }

  // 4) BAIXAR sem senha: baixa direto, sem pedir nada
  {
    baixados.length = 0
    const row = linha('e1', CERT_LIVRE, 1)
    const t = montar({ lista: [CERT_LIVRE] })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_LIVRE, 'baixar')
    assert.equal(t.pg.certAcao.value, null, 'sem campo de senha')
    await t.soltar()
    const [dl] = t.de('POST')
    assert.equal(dl.url, '/api/companies/e1/certificates/l/download')
    assert.deepEqual(dl.body, { password: null })
    assert.equal(dl.responseType, 'blob')
    assert.deepEqual(baixados, ['LIVRE.pfx'])
    assert.equal(t.pg.certBaixandoId.value, null)
  }

  // 5) BAIXAR com senha: pede a senha; vazia não vai; certa baixa (senha no corpo)
  {
    baixados.length = 0
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A, CERT_B] })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'baixar')
    assert.deepEqual(t.pg.certAcao.value, { tipo: 'baixar', id: 'a' })
    assert.equal(t.de('POST').length, 0, 'nada sai antes da senha')
    await t.pg.confirmarAcaoCertificado(row, CERT_A)
    assert.equal(t.de('POST').length, 0)
    assert.match(t.pg.certErro.value, /senha do certificado/)
    t.pg.certSenhaAcao.value = 'segredo'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    const [dl] = t.de('POST')
    assert.equal(dl.url, '/api/companies/e1/certificates/a/download')
    assert.deepEqual(dl.body, { password: 'segredo' })
    assert.ok(!dl.url.includes('segredo'), 'senha nunca na URL')
    assert.deepEqual(baixados, ['JLAS.pfx'])
    assert.equal(t.pg.certAcao.value, null, 'campo fecha depois de baixar')
    assert.equal(t.pg.certSenhaAcao.value, '', 'senha digitada não fica na memória da tela')
  }

  // 6) senha errada: o erro chega como ARQUIVO (responseType blob) e vira mensagem clara
  {
    baixados.length = 0
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A], responder: () => ({ erro: erroApi('senha_incorreta', true) }) })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'baixar')
    t.pg.certSenhaAcao.value = 'errada'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, 'Senha do certificado incorreta.')
    assert.deepEqual(baixados, [])
    assert.deepEqual(t.pg.certAcao.value, { tipo: 'baixar', id: 'a' }, 'campo continua aberto para corrigir')
  }
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A], responder: () => ({ erro: erroApi('muitas_tentativas', true) }) })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'baixar')
    t.pg.certSenhaAcao.value = 'x'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.match(t.pg.certErro.value, /15 minutos/)
  }
  {
    // A trava da tela venceu no meio: o erro-arquivo também tranca a tela.
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A], responder: () => ({ erro: erroApi('empresas_locked', true) }) })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'baixar')
    t.pg.certSenhaAcao.value = 'x'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.bloqueios(), 1)
  }

  // 7) EXCLUIR SENHA: pede a senha atual e manda junto; a lista mostra sem senha
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A, CERT_B], gridAntes: { rows: [row] } })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'tirar_senha')
    await t.pg.confirmarAcaoCertificado(row, CERT_A)
    assert.equal(t.de('PATCH').length, 0, 'sem a senha atual não manda')
    assert.match(t.pg.certErro.value, /senha atual/)
    t.pg.certSenhaAcao.value = 'atual'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    const [pt] = t.de('PATCH')
    assert.equal(pt.url, '/api/companies/e1/certificates/a')
    assert.deepEqual(pt.body, { password: null, current_password: 'atual' })
    assert.equal(t.pg.certLista.value.find((c) => c.id === 'a').has_password, false)
    assert.equal(t.pg.certLista.value.find((c) => c.id === 'b').has_password, true, 'só o escolhido')
    assert.equal(t.refreshes(), 1)
    assert.equal(t.pg.certAcao.value, null)
  }
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A], responder: () => ({ erro: erroApi('senha_incorreta') }) })
    await abrir(t, row)
    t.pg.pedirAcaoCertificado(row, CERT_A, 'tirar_senha')
    t.pg.certSenhaAcao.value = 'errada'
    const p = t.pg.confirmarAcaoCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, 'Senha do certificado incorreta.')
    assert.equal(t.pg.certLista.value[0].has_password, true)
    assert.equal(t.refreshes(), 0)
  }

  // 8) TROCAR a senha do mais novo (com senha) pede a atual e manda junto
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A] })
    await abrir(t, row)
    t.pg.certSenha.value = 'nova'
    await t.pg.salvarCertificado(row)
    assert.equal(t.de('PATCH').length, 0)
    assert.match(t.pg.certErro.value, /senha atual/)
    t.pg.certSenhaAtual.value = 'velha'
    const p = t.pg.salvarCertificado(row)
    await t.soltar(); await p
    assert.deepEqual(t.de('PATCH')[0].body, { password: 'nova', current_password: 'velha' })
  }
  {
    // Certificado sem senha: pôr a primeira senha não pede "atual".
    const row = linha('e1', CERT_LIVRE, 1)
    const t = montar({ lista: [CERT_LIVRE] })
    await abrir(t, row)
    t.pg.certSenha.value = 'primeira'
    const p = t.pg.salvarCertificado(row)
    await t.soltar(); await p
    assert.deepEqual(t.de('PATCH')[0].body, { password: 'primeira', current_password: null })
  }

  // 9) excluir o mais novo: confirma, apaga o certo, recarrega a tabela E a lista
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]),
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
    })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.pg.excluirCertificado(row, CERT_A) // clique repetido
    await t.soltar(); await p
    assert.deepEqual(t.deletes(), ['/api/companies/e1/certificates/a'])
    assert.match(t.confirmar[0], /JLAS\.pfx \(vence 27\/07\/2027\) de jlas/)
    assert.equal(t.confirmar.length, 1)
    assert.equal(t.refreshes(), 1)
    assert.equal(t.chamadas.filter((c) => c.url.endsWith('/certificates') && c.method === 'GET').length, 2, 'lista buscada de novo')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.pg.certAberto.value, 'e1', 'painel continua aberto para excluir o próximo')
  }

  // 10) excluir o MAIS ANTIGO: apaga exatamente ele e o selo continua no mais novo
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_A]), gridAntes: { rows: [row] } })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_B)
    await t.soltar(); await p
    assert.deepEqual(t.deletes(), ['/api/companies/e1/certificates/b'])
    assert.match(t.confirmar[0], /JLAS antigo\.pfx \(vence 01\/10\/2026\)/)
    assert.doesNotMatch(t.confirmar[0], /JLAS\.pfx \(/)
    assert.equal(t.grid.value.rows[0].certificado.id, 'a')
    assert.equal(t.grid.value.rows[0].certificado.total, 1)
  }

  // 11) excluir o último: lista vazia e selo some
  {
    const row = linha('e1', CERT_A, 1)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A] : []), gridAntes: { rows: [row] }, gridDepois: { rows: [linha('e1', null)] } })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.pg.certLista.value, [])
    assert.equal(t.grid.value.rows[0].certificado, null)
  }

  // 12) recarga da TABELA falha depois do DELETE: o apagado sai do painel e do selo mesmo assim
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]), gridAntes: { rows: [row] } })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.grid.value.rows[0].certificado.id, 'b', 'selo passa para o próximo')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
  }

  // 13) recarga da LISTA falha depois do DELETE: o apagado não fica com botões ativos
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : { erro: 'rede' }),
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
    })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
    assert.equal(t.pg.certErro.value, 'rede')
  }

  // 14) cancelou a confirmação: nada é apagado
  {
    const t = montar({ confirma: false })
    const row = linha('e1', CERT_A)
    await abrir(t, row)
    await t.pg.excluirCertificado(row, CERT_A)
    assert.deepEqual(t.deletes(), [])
    assert.equal(t.refreshes(), 0)
  }

  // 15) erro no DELETE aparece no painel, destrava e não mexe na tela; 404 = já excluído
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A, CERT_B], gridAntes: { rows: [row] }, responder: () => ({ erro: erroApi('boom') }) })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, 'boom')
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.refreshes(), 0)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['a', 'b'])
  }
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]),
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
      responder: () => ({ erro: erroApi('certificate_not_found') }),
    })
    await abrir(t, row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, null)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
  }

  // 16) lista que chega depois de trocar de empresa não aparece na outra
  {
    const CERT_Z = { ...CERT_A, id: 'z', filename: 'OUTRA.pfx' }
    const t = montar({ lista: (url) => (url.includes('/e1/') ? [CERT_A, CERT_B] : [CERT_Z]), lifo: true })
    t.pg.alternarCertificado(linha('e1', CERT_A))
    t.pg.alternarCertificado(linha('e2', CERT_Z))
    await t.soltar()
    assert.equal(t.pg.certAberto.value, 'e2')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['z'])
  }

  // 17) nada roda junto: salvar/baixar/excluir esperam a ação em andamento
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A] })
    await abrir(t, row)
    t.pg.certExcluindo.value = 'a'
    t.pg.certSenha.value = 'x'
    t.pg.certSenhaAtual.value = 'y'
    await t.pg.salvarCertificado(row)
    t.pg.pedirAcaoCertificado(row, CERT_LIVRE, 'baixar')
    await t.soltar()
    assert.equal(t.chamadas.filter((c) => c.method !== 'GET').length, 0)
  }

  console.log('PASS: vencimento DD/MM/AAAA; baixar com trava de senha (senha no corpo, erro-arquivo traduzido); excluir/trocar senha pedem a atual; senha guardada não aparece; excluir certificado seguro; troca de empresa')
})().catch((e) => { console.error(e); process.exit(1) })
