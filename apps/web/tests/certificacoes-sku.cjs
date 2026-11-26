// Run from apps/web: node tests/certificacoes-sku.cjs
// M-69 (26/11/2026): coluna SKU da aba Importação › Certificações, entre
// Produto e Modelo. Mostrada como "dg017, dg018, dg019"; editável como as
// outras células (salva ao sair do campo); sempre à mostra (fora do Layout);
// vai para o Excel logo depois do Produto. SKU em outra certificação: o
// servidor não grava (409) e a célula volta ao que era, com a frase do
// servidor; SKU que não existe em produto nenhum: grava e avisa.
// Usa o page()/render() de certificacoes-anatel.cjs (o <script setup> de
// verdade com api falsa).
const assert = require('node:assert/strict')
const { page, row, render, settle } = require('./certificacoes-anatel.cjs')

const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const campo = (valor) => ({ value: valor })
// As células SKU da tabela, em ordem: o <input> inteiro.
const celulasSku = (html) => html.match(/<input[^>]*data-cert-skus[^>]*>/g) || []

async function run() {
  // ---------- coluna entre Produto e Modelo, mostrada com vírgula
  {
    const { state: s } = page()
    s.rows.value = [row({ skus: ['dg017', 'dg018', 'dg019'] }), row({ id: 'two', skus: [] })]
    const html = semComentarios(await render(s))
    const thead = html.slice(html.indexOf('<thead>'), html.indexOf('</thead>'))
    const titulos = [...thead.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1].trim())
    assert.deepEqual(titulos.slice(0, 3), ['Produto', 'SKU', 'Modelo'])
    assert.match(thead, /<th class="text-left" data-col-skus title="SKUs dos produtos deste certificado, sem a tag/)
    const [primeira, segunda] = celulasSku(html)
    assert.match(primeira, /value="dg017, dg018, dg019"/)
    assert.match(primeira, /class="cell-input"/, 'mesma cara das outras células')
    assert.doesNotMatch(primeira, /disabled/)
    assert.match(segunda, /class="cell-input" value placeholder/, "vazio")
    assert.match(segunda, /placeholder="—"/)
    // Sempre à mostra: não entra no Layout.
    assert.ok(!s.COLUNAS_LAYOUT.some((c) => c.chave === 'skus'))
    s.alternarColuna('modelo')
    assert.equal(celulasSku(semComentarios(await render(s))).length, 2)
    // Cartão do celular também mostra.
    assert.match(html, /data-cartao-skus>dg017, dg018, dg019<\/dd>/)
    assert.match(html, /data-cartao-skus>—<\/dd>/)
  }

  // ---------- salvar: PATCH só com o texto; a resposta manda o que ficou
  {
    const { state: s, calls } = page()
    const current = row({ skus: ['dg017'] })
    s.rows.value = [current]
    const el = campo('dg017, dg019.ra; DG018')
    const salvando = s.salvarSkus(current, el)
    await settle()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/api/financeiro/suprimentos/one')
    assert.deepEqual(calls[0].options, { method: 'PATCH', body: { skus: 'dg017, dg019.ra; DG018' } })
    assert.equal(s.skusSalvando.one, true)
    assert.equal(s.hasBusyRow.value, true, 'Atualizar e Excel esperam')
    calls[0].resolve({ ...current, produto: 'do servidor', skus: ['dg017', 'dg018', 'dg019'], avisos: [] })
    await salvando
    assert.deepEqual(current.skus, ['dg017', 'dg018', 'dg019'])
    assert.equal(el.value, 'dg017, dg018, dg019', 'a célula mostra o que ficou gravado (sem a tag)')
    assert.equal(current.produto, 'Telefone', 'só o SKU vem da resposta')
    assert.equal(s.hasBusyRow.value, false)
    assert.equal(s.avisoSkus.value, null)
    assert.equal(s.errorText.value, null)
    // Igual ao que já está: nada a salvar.
    s.salvarSkus(current, campo('dg017, dg018, dg019 '))
    assert.equal(calls.length, 1)
  }

  // ---------- SKU que não existe em produto nenhum: grava e avisa
  {
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    const salvando = s.salvarSkus(current, campo('dg017, dg999'))
    await settle()
    calls[0].resolve({ ...current, skus: ['dg017', 'dg999'], avisos: ['dg999 não existe em nenhum produto'] })
    await salvando
    assert.deepEqual(current.skus, ['dg017', 'dg999'])
    assert.equal(s.avisoSkus.value, 'SKU: dg999 não existe em nenhum produto. Foi gravado assim mesmo.')
    const html = semComentarios(await render(s))
    assert.match(html, /<p role="status" class="text-xs text-amber-700 dark:text-amber-400" data-aviso-skus>SKU: dg999 não existe em nenhum produto\. Foi gravado assim mesmo\.<\/p>/)
  }

  // ---------- SKU em outra certificação: nada grava, a célula volta e avisa onde está
  {
    const { state: s, calls } = page()
    const current = row({ id: 'usm002', skus: ['dg046'] })
    s.rows.value = [current]
    const el = campo('dg046, dg019')
    const salvando = s.salvarSkus(current, el)
    await settle()
    const erro = new Error('409')
    erro.data = { detail: { code: 'sku_em_outro_certificado', message: 'dg019 já está na certificação USM001' } }
    calls[0].reject(erro)
    await salvando
    assert.deepEqual(current.skus, ['dg046'])
    assert.equal(el.value, 'dg046', 'a célula volta ao que era')
    assert.equal(s.errorText.value, 'SKU: dg019 já está na certificação USM001. Nada foi gravado.')
    const html = semComentarios(await render(s))
    assert.match(html, /role="alert"[^>]*>SKU: dg019 já está na certificação USM001\. Nada foi gravado\./)
    // Corrigindo, o aviso sai.
    const salvando2 = s.salvarSkus(current, campo('dg046, dg047'))
    await settle()
    calls[1].resolve({ ...current, skus: ['dg046', 'dg047'], avisos: [] })
    await salvando2
    assert.equal(s.errorText.value, null)
    // Outro erro (rede): frase genérica, célula volta.
    const el3 = campo('dg048')
    const salvando3 = s.salvarSkus(current, el3)
    await settle()
    calls[2].reject(new Error('rede'))
    await salvando3
    assert.equal(el3.value, 'dg046, dg047')
    assert.equal(s.errorText.value, 'SKU: não foi possível salvar. Tente novamente.')
  }

  // ---------- as outras edições pendentes da linha vão antes
  {
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'modelo', 'USM009')
    const salvando = s.salvarSkus(current, campo('dg050'))
    await settle()
    assert.equal(calls.length, 1)
    assert.deepEqual(calls[0].options.body, { modelo: 'USM009' }, 'o salvamento automático primeiro')
    calls[0].resolve({ ...current })
    await settle()
    assert.equal(calls.length, 2)
    assert.deepEqual(calls[1].options.body, { skus: 'dg050' })
    calls[1].resolve({ ...current, skus: ['dg050'], avisos: [] })
    await salvando
    assert.deepEqual(current.skus, ['dg050'])
  }

  // ---------- quem só vê não edita
  {
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true } } } })
    const current = row({ skus: ['dg017'] })
    s.rows.value = [current]
    const el = campo('dg099')
    await s.salvarSkus(current, el)
    assert.equal(calls.length, 0)
    assert.equal(el.value, 'dg017')
    assert.deepEqual(current.skus, ['dg017'])
    const [celula] = celulasSku(semComentarios(await render(s)))
    assert.match(celula, /disabled/)
  }

  // ---------- Excel: SKU depois do Produto, como na tela
  {
    const { state: s, planilhas } = page()
    s.rows.value = [row({ skus: ['dg017', 'dg018'] }), row({ id: 'two', skus: [] })]
    await s.exportTable()
    assert.deepEqual(planilhas[0].linhas[0].slice(0, 3), ['Produto', 'SKU', 'Modelo'])
    assert.deepEqual(planilhas[0].linhas.slice(1).map((l) => l[1]), ['dg017, dg018', ''])
  }

  console.log('PASS: M-69 — coluna SKU (mostrar, salvar sem a tag, 409, aviso, Excel, quem só vê)')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
