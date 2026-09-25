// Run from apps/web: node tests/companies-certificado-excluir.cjs
// Cadastros › Empresas (25/09/2026), Eduardo: "no certificado digital só
// aparece o mês e o ano que vence, precisa aparecer o dia [...] um botão para
// excluir o certificado". Travas:
//  - o selo mostra DD/MM/AAAA; no próprio dia do vencimento ainda "vence"
//    (amarelo), "venceu" só a partir do dia seguinte;
//  - o painel lista TODOS os certificados da empresa (renovação subida em dobro)
//    e cada um tem o seu "excluir", que pede confirmação e apaga AQUELE;
//  - o apagado sai da tela na hora, mesmo se a recarga da tabela ou da lista falhar;
//  - clique repetido não manda dois DELETE; salvar não roda no meio de exclusão;
//    404 "já excluído" conta como excluído;
//  - a resposta de uma empresa não aparece no painel de outra aberta depois;
//  - a senha revelada some ao excluir e a de um certificado apagado não aparece.
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

// --- peças exigidas do template
{
  const tpl = descriptor.template.content
  assert.match(tpl, /v-for="\(c, i\) in certificadosDoPainel\(row\)"/, 'painel lista todos os certificados')
  assert.match(tpl, /@click="excluirCertificado\(row, c\)"/, 'cada certificado tem o seu excluir')
  assert.match(tpl, /:disabled="!!certExcluindo \|\| certSalvando"/, 'excluir travado durante outra ação')
  assert.match(tpl, /:disabled="certSalvando \|\| !!certExcluindo"/, 'salvar travado durante exclusão')
  assert.match(tpl, /:disabled="!!certExcluindo"\s*@click="mostrarSenhaGuardada\(row\)"/, 'ver senha travado durante exclusão')
  assert.match(tpl, /max-h-48 overflow-y-auto/, 'lista longa rola dentro do painel')
  assert.doesNotMatch(tpl, /troca de arquivo abaixo valem/, 'subir arquivo acrescenta, não troca')
}

// --- lógica: o bloco do certificado da página, como está
const script = descriptor.scriptSetup.content
const de = '// ---------- certificado digital ----------'
const ate = '// ---------- inline obs edit ----------'
assert.ok(script.includes(de) && script.includes(ate), 'marcadores do bloco do certificado')
const trecho = script.slice(script.indexOf(de), script.indexOf(ate))
const js = ts.transpileModule(trecho, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

// lista: array fixo, ou função (url, n) com n = quantas GET de lista já houve
// deleteErro: código do erro do DELETE (null = 204)
// gridDepois: o que o refresh() põe na tabela; undefined = refresh falha (grid fica como estava)
function montar({ lista = [], gridAntes = null, gridDepois, deleteErro = null, confirma = true, lifo = false, senha = 'x' } = {}) {
  const chamadas = []
  let refreshes = 0
  let listas = 0
  const grid = ref(gridAntes)
  const aguardando = []
  const apiE = (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET' })
    return new Promise((resolve, reject) => {
      aguardando.push(() => {
        if (opts.method === 'DELETE') return deleteErro ? reject({ data: { detail: { code: deleteErro } } }) : resolve(null)
        if (url.endsWith('/password')) return resolve({ password: senha })
        if (url.endsWith('/certificates')) {
          const r = typeof lista === 'function' ? lista(url, listas++) : lista
          return r instanceof Error ? reject({ data: { detail: { code: r.message } } }) : resolve(r)
        }
        resolve({})
      })
    })
  }
  const mensagemDeErro = (e, padrao) => e?.data?.detail?.code || padrao
  const refresh = async () => { refreshes++; if (gridDepois !== undefined) grid.value = gridDepois }
  const confirmar = []
  const confirm = (msg) => { confirmar.push(msg); return confirma }
  const factory = new Function(
    'ref', 'apiE', 'mensagemDeErro', 'refresh', 'grid', 'confirm',
    js + '\nreturn { certAberto, certLista, certExcluindo, certSalvando, certSenhaGuardada, certErro, alternarCertificado, certificadosDoPainel, excluirCertificado, salvarCertificado, mostrarSenhaGuardada, vencimentoCertificado, dataBR }',
  )
  const pg = factory(ref, apiE, mensagemDeErro, refresh, grid, confirm)
  // Resolve tudo o que está pendurado, inclusive o que for pedido no caminho.
  const soltar = async () => {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setImmediate(r))
      if (!aguardando.length) continue
      const lote = aguardando.splice(0)
      ;(lifo ? lote.reverse() : lote).forEach((f) => f())
    }
  }
  return { pg, grid, chamadas, confirmar, soltar, refreshes: () => refreshes, deletes: () => chamadas.filter((c) => c.method === 'DELETE').map((c) => c.url) }
}

const CERT_A = { id: 'a', filename: 'JLAS.pfx', has_password: true, expires_at: '2027-07-27' }
const CERT_B = { id: 'b', filename: 'JLAS antigo.pfx', has_password: true, expires_at: '2026-10-01' }
const linha = (id, cert, total = 2) => ({ company: { id, apelido: 'jlas' }, stores: {}, certificado: cert ? { ...cert, total } : null })
const isoDaqui = (d) => {
  const x = new Date()
  x.setDate(x.getDate() + d)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
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

  // 4) excluir o mais novo: confirma, apaga o certo, recarrega a tabela E a lista
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]),
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
    })
    t.pg.alternarCertificado(row)
    await t.soltar()
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['a', 'b'])
    t.pg.certSenhaGuardada.value = 'segredo'
    const p = t.pg.excluirCertificado(row, CERT_A)
    // clique repetido enquanto o primeiro está no ar: nada de segundo DELETE
    await t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.deletes(), ['/api/companies/e1/certificates/a'])
    assert.match(t.confirmar[0], /JLAS\.pfx \(vence 27\/07\/2027\) de jlas/)
    assert.equal(t.confirmar.length, 1)
    assert.equal(t.refreshes(), 1)
    assert.equal(t.chamadas.filter((c) => c.url.endsWith('/certificates') && c.method === 'GET').length, 2, 'lista buscada de novo')
    assert.equal(t.pg.certSenhaGuardada.value, null, 'senha revelada some ao excluir')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.pg.certAberto.value, 'e1', 'painel continua aberto para excluir o próximo')
  }

  // 5) excluir o MAIS ANTIGO: apaga exatamente ele e o selo da tabela continua no mais novo
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_A]), gridAntes: { rows: [row] } })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_B)
    await t.soltar(); await p
    assert.deepEqual(t.deletes(), ['/api/companies/e1/certificates/b'])
    assert.match(t.confirmar[0], /JLAS antigo\.pfx \(vence 01\/10\/2026\)/)
    assert.doesNotMatch(t.confirmar[0], /JLAS\.pfx \(/)
    assert.equal(t.grid.value.rows[0].certificado.id, 'a')
    assert.equal(t.grid.value.rows[0].certificado.total, 1)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['a'])
  }

  // 6) excluir o último: lista vazia e selo some (painel volta para "subir arquivo")
  {
    const row = linha('e1', CERT_A, 1)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A] : []), gridAntes: { rows: [row] }, gridDepois: { rows: [linha('e1', null)] } })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.pg.certLista.value, [])
    assert.equal(t.grid.value.rows[0].certificado, null)
  }

  // 7) recarga da TABELA falha depois do DELETE: o apagado sai do painel e do selo mesmo assim
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]), gridAntes: { rows: [row] } /* refresh falha */ })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.grid.value.rows[0].certificado.id, 'b', 'selo passa para o próximo')
    assert.equal(t.grid.value.rows[0].certificado.total, 1)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
  }

  // 8) recarga da LISTA falha depois do DELETE: o apagado não fica com "excluir" ativo
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : new Error('rede')),
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
    })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
    assert.equal(t.pg.certErro.value, 'rede', 'avisa que a lista não veio')
  }

  // 9) cancelou a confirmação: nada é apagado
  {
    const t = montar({ confirma: false })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    await t.soltar()
    await t.pg.excluirCertificado(row, CERT_A)
    assert.deepEqual(t.deletes(), [])
    assert.equal(t.refreshes(), 0)
  }

  // 10) erro no DELETE aparece no painel, destrava o botão e não mexe na tela
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A, CERT_B], deleteErro: 'boom', gridAntes: { rows: [row] } })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, 'boom')
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.refreshes(), 0)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['a', 'b'])
    assert.equal(t.grid.value.rows[0].certificado.id, 'a')
  }

  // 11) DELETE 404 (já excluído em outra aba): tratado como excluído, sem erro
  {
    const row = linha('e1', CERT_A)
    const t = montar({
      lista: (url, n) => (n === 0 ? [CERT_A, CERT_B] : [CERT_B]),
      deleteErro: 'certificate_not_found',
      gridAntes: { rows: [row] },
      gridDepois: { rows: [linha('e1', CERT_B, 1)] },
    })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, null)
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
  }

  // 12) lista que chega depois de trocar de empresa não aparece na outra
  //     (a resposta da e1 chega por último, por cima da e2)
  {
    const CERT_Z = { ...CERT_A, id: 'z', filename: 'OUTRA.pfx' }
    const t = montar({ lista: (url) => (url.includes('/e1/') ? [CERT_A, CERT_B] : [CERT_Z]), lifo: true })
    t.pg.alternarCertificado(linha('e1', CERT_A))
    t.pg.alternarCertificado(linha('e2', CERT_Z)) // abriu outra antes da resposta
    await t.soltar()
    assert.equal(t.pg.certAberto.value, 'e2')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['z'])
  }
  {
    const t = montar({ lista: [CERT_A, CERT_B] })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    t.pg.alternarCertificado(row) // fechou antes da resposta
    await t.soltar()
    assert.equal(t.pg.certLista.value, null)
  }

  // 13) senha que chega depois do certificado ser excluído não aparece no painel
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A, CERT_B], gridAntes: { rows: [row] }, senha: 'da-A' })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const pedido = t.pg.mostrarSenhaGuardada(row) // sai o GET da senha da A
    t.grid.value = { rows: [linha('e1', CERT_B, 1)] } // A excluída e tabela recarregada antes da resposta
    await t.soltar(); await pedido
    assert.equal(t.pg.certSenhaGuardada.value, null)
  }
  {
    const row = linha('e1', CERT_A)
    const t = montar({ lista: [CERT_A], gridAntes: { rows: [row] }, senha: 'da-A' })
    t.pg.alternarCertificado(row)
    await t.soltar()
    const pedido = t.pg.mostrarSenhaGuardada(row)
    await t.soltar(); await pedido
    assert.equal(t.pg.certSenhaGuardada.value, 'da-A', 'caminho normal continua mostrando')
  }

  // 14) salvar não roda no meio de uma exclusão
  {
    const t = montar()
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    t.pg.certExcluindo.value = 'a'
    await t.pg.salvarCertificado(row)
    assert.equal(t.chamadas.filter((c) => c.method !== 'GET').length, 0)
  }

  console.log('PASS: vencimento DD/MM/AAAA (dia do vencimento ainda vence); painel lista todos; excluir apaga o certo, confirma, não duplica, sai da tela mesmo com recarga falhando, 404 = já excluído; troca de empresa; senha de apagado não aparece')
})().catch((e) => { console.error(e); process.exit(1) })
