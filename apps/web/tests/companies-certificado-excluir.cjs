// Run from apps/web: node tests/companies-certificado-excluir.cjs
// Cadastros › Empresas (25/09/2026), Eduardo: "no certificado digital só
// aparece o mês e o ano que vence, precisa aparecer o dia [...] um botão para
// excluir o certificado". Travas:
//  - o selo mostra DD/MM/AAAA (e continua marcando vencido / vencendo em 30 dias);
//  - o painel lista TODOS os certificados da empresa (renovação subida em dobro)
//    e cada um tem o seu "excluir", que pede confirmação antes de apagar;
//  - clique repetido não manda dois DELETE; salvar não roda no meio de exclusão;
//  - a resposta de uma empresa não aparece no painel de outra aberta depois;
//  - a senha revelada some ao excluir (podia ser a do certificado apagado).
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
}

// --- lógica: o bloco do certificado da página, como está
const script = descriptor.scriptSetup.content
const de = '// ---------- certificado digital ----------'
const ate = '// ---------- inline obs edit ----------'
assert.ok(script.includes(de) && script.includes(ate), 'marcadores do bloco do certificado')
const trecho = script.slice(script.indexOf(de), script.indexOf(ate))
const js = ts.transpileModule(trecho, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function montar({ lista = [], gridDepois = null, falhaDelete = false, confirma = true, lifo = false } = {}) {
  const chamadas = []
  let refreshes = 0
  const grid = ref(null)
  const aguardando = []
  const apiE = (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET' })
    return new Promise((resolve, reject) => {
      aguardando.push(() => {
        if (opts.method === 'DELETE') return falhaDelete ? reject({ data: { detail: { code: 'boom' } } }) : resolve(null)
        if (url.endsWith('/certificates')) return resolve(typeof lista === 'function' ? lista(url) : lista)
        resolve({})
      })
    })
  }
  const mensagemDeErro = (e, padrao) => e?.data?.detail?.code || padrao
  const refresh = async () => { refreshes++; grid.value = gridDepois }
  const confirmar = []
  const confirm = (msg) => { confirmar.push(msg); return confirma }
  const factory = new Function(
    'ref', 'apiE', 'mensagemDeErro', 'refresh', 'grid', 'confirm',
    js + '\nreturn { certAberto, certLista, certExcluindo, certSalvando, certSenhaGuardada, certErro, alternarCertificado, certificadosDoPainel, excluirCertificado, salvarCertificado, vencimentoCertificado, dataBR }',
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
  return { pg, chamadas, confirmar, soltar, refreshes: () => refreshes }
}

const CERT_A = { id: 'a', filename: 'JLAS.pfx', has_password: false, expires_at: '2027-07-27' }
const CERT_B = { id: 'b', filename: 'JLAS antigo.pfx', has_password: true, expires_at: '2026-10-01' }
const linha = (id, cert) => ({ company: { id, apelido: 'jlas' }, stores: {}, certificado: cert ? { ...cert, total: 2 } : null })

;(async () => {
  // 1) data completa no selo, com vencido / vencendo
  {
    const { pg } = montar()
    assert.equal(pg.dataBR('2027-01-15'), '15/01/2027')
    assert.equal(pg.vencimentoCertificado({ expires_at: '2027-01-15' }).texto, '15/01/2027')
    assert.equal(pg.vencimentoCertificado({ expires_at: null }), null)
    assert.equal(pg.vencimentoCertificado({ expires_at: '2020-03-02' }).vencido, true)
    const daqui10 = new Date(Date.now() + 10 * 86_400_000)
    const iso = `${daqui10.getFullYear()}-${String(daqui10.getMonth() + 1).padStart(2, '0')}-${String(daqui10.getDate()).padStart(2, '0')}`
    const v = pg.vencimentoCertificado({ expires_at: iso })
    assert.equal(v.vencido, false)
    assert.equal(v.vencendo, true)
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

  // 4) excluir: confirma, apaga o certificado certo, recarrega a tabela e a lista
  {
    const restante = linha('e1', CERT_B)
    const t = montar({ lista: [CERT_B], gridDepois: { rows: [restante] } })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    await t.soltar()
    t.pg.certSenhaGuardada.value = 'segredo'
    const p = t.pg.excluirCertificado(row, CERT_A)
    // clique repetido enquanto o primeiro está no ar: nada de segundo DELETE
    await t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    const deletes = t.chamadas.filter((c) => c.method === 'DELETE')
    assert.deepEqual(deletes.map((c) => c.url), ['/api/companies/e1/certificates/a'])
    assert.match(t.confirmar[0], /JLAS\.pfx \(vence 27\/07\/2027\) de jlas/)
    assert.equal(t.refreshes(), 1)
    assert.equal(t.pg.certSenhaGuardada.value, null, 'senha revelada some ao excluir')
    assert.deepEqual(t.pg.certLista.value.map((c) => c.id), ['b'])
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.pg.certAberto.value, 'e1', 'painel continua aberto para excluir o próximo')
  }

  // 5) excluir o último: lista fica vazia (painel volta para "subir arquivo")
  {
    const t = montar({ gridDepois: { rows: [linha('e1', null)] } })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.deepEqual(t.pg.certLista.value, [])
  }

  // 6) cancelou a confirmação: nada é apagado
  {
    const t = montar({ confirma: false })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    await t.soltar()
    await t.pg.excluirCertificado(row, CERT_A)
    assert.equal(t.chamadas.filter((c) => c.method === 'DELETE').length, 0)
    assert.equal(t.refreshes(), 0)
  }

  // 7) erro no DELETE aparece no painel e destrava o botão
  {
    const t = montar({ falhaDelete: true })
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    const p = t.pg.excluirCertificado(row, CERT_A)
    await t.soltar(); await p
    assert.equal(t.pg.certErro.value, 'boom')
    assert.equal(t.pg.certExcluindo.value, null)
    assert.equal(t.refreshes(), 0)
  }

  // 8) lista que chega depois de trocar de empresa não aparece na outra
  //    (a resposta da e1 chega por último, por cima da e2)
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

  // 9) salvar não roda no meio de uma exclusão
  {
    const t = montar()
    const row = linha('e1', CERT_A)
    t.pg.alternarCertificado(row)
    t.pg.certExcluindo.value = 'a'
    await t.pg.salvarCertificado(row)
    assert.equal(t.chamadas.filter((c) => c.method !== 'GET').length, 0)
  }

  console.log('PASS: vencimento DD/MM/AAAA; painel lista todos; excluir confirma, não duplica, recarrega, limpa senha; erro e troca de empresa')
})().catch((e) => { console.error(e); process.exit(1) })
