// Run from apps/web: node tests/certificacoes-inmetro.cjs
const assert = require('node:assert/strict')
const { page, row, source: anatelSource, status, render, settle } = require('./certificacoes-anatel.cjs')

const source = (values = {}) => ({
  chave: 'MAKISA|MODERNA-0069/25|UAF001-M1', cnpj: '40191104000145',
  nome_empresa: 'MAKISA TRADING LTDA', certificador: 'MODERNA CERTIFICADORA',
  numero: 'MODERNA-0069/25', modelo: 'UAF001-M1', marca: 'URANYX',
  descricao: 'Fritadeira elétrica', produto: 'Fritadeira', inicio: null, fim: null,
  situacao_certificado: 'Ativo', alertas: ['Validade não informada no ProdCert.'],
  campos_confirmados: ['modelo', 'certificado', 'numero'], ...values,
})
const linked = (values = {}) => row({
  produto: 'airfryer', modelo: 'UAF001-M1', nome_comercial: 'Nome da equipe', certificado: 'inmetro',
  numero: 'MODERNA-0069/25', inicio: '2025-03-08', fim: '2030-03-08',
  inmetro_chave: 'MAKISA|MODERNA-0069/25|UAF001-M1', inmetro_dados: source(),
  inmetro_encontrado: true, inmetro_consultado_em: '2026-09-14T19:00:00Z', ...values,
})

async function run() {
  {
    const { state: s, calls } = page()
    s.anatelStatus.value = status()
    s.inmetroStatus.value = status({ ultimo_sucesso_em: '2026-09-13T19:00:00Z' })
    const loading = s.load()
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([linked()])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).reject(new Error('offline'))
    await loading
    assert.equal(s.rows.value.length, 1)
    assert.equal(s.errorText.value, null)
    assert.equal(s.statusError.value, null)
    assert.match(s.inmetroStatusError.value, /Inmetro/)
    assert.equal(s.anatelStatus.value.ultimo_sucesso_em, '2026-09-14T13:00:00Z')
    assert.equal(s.inmetroStatus.value.ultimo_sucesso_em, '2026-09-13T19:00:00Z')
  }
  {
    const { state: s, calls } = page()
    const loading = s.load()
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([linked()])
    calls.find(c => c.url.endsWith('/anatel/status')).reject(new Error('offline'))
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status({ source_updated_at: null }))
    await loading
    assert.match(s.statusError.value, /Anatel/)
    assert.equal(s.inmetroStatusError.value, null)
    assert.equal(s.rows.value.length, 1)
    assert.equal(s.inmetroStatus.value.source_updated_at, null)
    assert.doesNotMatch(await render(s), /Base publicada em/)
  }
  {
    const { state: s, calls } = page()
    const current = linked()
    s.rows.value = [current]
    s.anatelStatus.value = status()
    s.scheduleSave(current, 'produto', 'Nome amigável')
    s.scheduleSave(current, 'nome_comercial', 'Nome comercial manual')
    s.scheduleSave(current, 'valor', 25000)
    const syncing = s.syncInmetro()
    await s.syncInmetro()
    await s.syncAnatel()
    await s.exportTable()
    assert.equal(calls.length, 1, 'all synchronization/export actions must wait for the same autosave')
    assert.deepEqual(calls[0].options.body, {
      produto: 'Nome amigável', nome_comercial: 'Nome comercial manual', valor: 25000,
    })
    calls[0].resolve()
    await settle()
    assert.equal(calls[1].url, '/api/financeiro/suprimentos/inmetro/sincronizar')
    assert.equal(calls[1].options.method, 'POST')
    calls[1].resolve({ status: 'ok', criados: 2, atualizados: 3, nao_localizados: 1, conflitos: 1 })
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await syncing
    assert.equal(calls.filter(c => c.url.includes('/anatel/')).length, 0)
    assert.match(s.inmetroSyncMessage.value, /2 novos registros e 3 atualizados/)
    assert.match(s.inmetroSyncMessage.value, /dados anteriores preservados/)
    assert.match(s.inmetroSyncMessage.value, /conferência/)
    assert.equal(s.synchronizingInmetro.value, false)
    assert.equal(s.rows.value[0].nome_comercial, 'Nome comercial manual')
    assert.equal(s.rows.value[0].pdf_nome, 'certificado.pdf')
    assert.equal(s.anatelStatus.value.ultimo_sucesso_em, '2026-09-14T13:00:00Z')
  }
  {
    const { state: s, calls } = page()
    const current = linked()
    s.rows.value = [current]
    s.scheduleSave(current, 'inicio', '2026-09-01')
    const syncing = s.syncInmetro()
    calls[0].reject(new Error('save failed'))
    await syncing
    assert.equal(calls.length, 1)
    assert.equal(s.rows.value[0].inicio, '2026-09-01')
    assert.match(s.inmetroSyncError.value, /não foram salvas/)
  }
  {
    const { state: s, calls } = page()
    const current = linked()
    for (const field of ['modelo', 'certificado', 'numero']) {
      const before = current[field]
      s.scheduleSave(current, field, 'manual override')
      assert.equal(current[field], before)
      assert.equal(s.isOfficialField(current, field), true)
    }
    for (const field of ['inicio', 'fim', 'nome_comercial', 'produto', 'valor']) {
      assert.equal(s.isOfficialField(current, field), false, `${field} must remain editable if not confirmed`)
    }
    const dated = linked({ inmetro_dados: source({
      inicio: '2025-08-03', fim: '2027-08-03',
      campos_confirmados: ['modelo', 'certificado', 'numero', 'inicio', 'fim'],
    }) })
    for (const field of ['inicio', 'fim']) {
      s.scheduleSave(dated, field, '2020-01-01')
      assert.notEqual(dated[field], '2020-01-01')
      assert.equal(s.isOfficialField(dated, field), true)
    }
    for (const dados of [null, source({ campos_confirmados: [] })]) {
      const incomplete = linked({ inmetro_dados: dados })
      for (const field of ['modelo', 'certificado', 'numero']) {
        const before = incomplete[field]
        s.scheduleSave(incomplete, field, 'manual override')
        assert.equal(incomplete[field], before, 'linked identity stays protected even without source confirmation metadata')
        assert.equal(s.isOfficialField(incomplete, field), true)
      }
    }
    for (const missingDate of [null, undefined, '']) {
      const incomplete = linked({ inmetro_dados: source({
        inicio: missingDate, fim: missingDate, campos_confirmados: ['inicio', 'fim'],
      }) })
      for (const field of ['inicio', 'fim']) {
        assert.equal(s.isOfficialField(incomplete, field), false, 'confirmation metadata without a source date must not lock a manual date')
        s.scheduleSave(incomplete, field, '2020-01-01')
        assert.equal(incomplete[field], '2020-01-01')
      }
      s.rows.value = [incomplete]
      const html = await render(s)
      assert.doesNotMatch(html, /<input[^>]*type="date"[^>]*readonly/, 'missing source dates remain editable in the rendered table')
    }
    const unconfirmedDates = linked({ inmetro_dados: source({ inicio: '2025-08-03', fim: '2027-08-03' }) })
    for (const field of ['inicio', 'fim']) assert.equal(s.isOfficialField(unconfirmedDates, field), false)
    assert.equal(s.isOfficialField(linked({ inmetro_dados: source({ campos_confirmados: ['produto', 'valor', 'nome_comercial'] }) }), 'nome_comercial'), false)
    const anatel = row({ anatel_numero: '071972618234', anatel_dados: anatelSource() })
    assert.equal(s.isOfficialField(anatel, 'nome_comercial'), true, 'Anatel rules stay unchanged')
    await s.removeRow(current)
    assert.equal(calls.length, 0, 'automatic records cannot be deleted or altered in guarded fields')
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [linked()]
    s.anatelStatus.value = status()
    s.inmetroStatus.value = status()
    const syncing = s.syncInmetro()
    await settle()
    calls[0].resolve({ status: 'error', erro: 'private source detail' })
    await settle()
    calls[1].resolve(status({ erro: 'private source detail' }))
    await syncing
    assert.equal(s.rows.value.length, 1)
    assert.equal(s.statusError.value, null)
    assert.equal(s.errorText.value, null)
    assert.match(s.inmetroSyncError.value, /dados anteriores foram preservados/)
    assert.doesNotMatch(await render(s), /private source detail/)
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [linked()]
    const syncing = s.syncInmetro()
    await settle()
    calls[0].reject(new Error('network failed'))
    await settle()
    calls[1].reject(new Error('offline'))
    await syncing
    assert.equal(s.rows.value.length, 1)
    assert.match(s.inmetroSyncError.value, /Tente atualizar novamente/)
    assert.match(s.inmetroStatusError.value, /Inmetro/)
    assert.equal(s.busy.value, false)
  }
  {
    const { state: s, calls } = page()
    const syncing = s.syncInmetro()
    await settle()
    calls[0].resolve({ status: 'busy' })
    await settle()
    calls[1].resolve(status())
    await syncing
    assert.equal(calls.length, 2)
    assert.match(s.inmetroSyncMessage.value, /já está em andamento/)
    assert.equal(s.busy.value, false)
  }
  {
    const { state: s } = page()
    const current = linked()
    s.rows.value = [current]
    s.selectedRow.value = current
    const html = await render(s)
    assert.match(html, /Situação oficial/)
    assert.match(html, /Inmetro · Ativo/)
    assert.match(html, /UAF001-M1/)
    assert.match(html, /MODERNA CERTIFICADORA/)
    assert.match(html, /40.191.104\/0001-45/)
    assert.match(html, /Atualizar agora/)
    assert.match(html, /Atualizar Inmetro/)
    assert.match(html, /PDF do certificado/)
    assert.equal((html.match(/readonly/g) || []).length, 2, 'only number and model text inputs are read-only')
    const dialog = html.slice(html.indexOf('<dialog'))
    assert.match(dialog, /Não informada pelo ProdCert/)
    assert.doesNotMatch(dialog, /08\/03\/2025|08\/03\/2030/, 'source details must never show manual dates as official')
    assert.match(dialog, /http:\/\/www.inmetro.gov.br\/prodcert\//)
    s.selectedRow.value = linked({ inmetro_encontrado: false })
    assert.match(await render(s), /isso não confirma cancelamento ou irregularidade/)
    assert.doesNotMatch(s.officialSituationClass(s.selectedRow.value), /emerald/)
  }
  {
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true } } } })
    s.rows.value = [linked()]
    await s.syncInmetro()
    assert.equal(calls.length, 0)
    const html = await render(s)
    assert.doesNotMatch(html, /Atualizar Inmetro|Atualizar agora/)
    assert.match(html, /Baixar tabela em PDF/)
    assert.match(html, /Ver dados de Inmetro/)
  }
  console.log('PASS: ten Inmetro source isolation, autosave, confirmed fields, permissions and rendered UI scenarios')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
