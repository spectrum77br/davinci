// Run from apps/web: node tests/pricing-certificado-garantia.cjs
// Garantia e Validade pelo certificado na aba Produtos (06/11/2026, Eduardo:
// "tem que deixar opção para editar a validade, e em certificações adicione o
// campo garantia, e aí ele passa a mudar de acordo com o certificado"; "deixe
// a opção de editar na tabela de preços"). Roda os helpers REAIS da página
// (títulos, leitura da data, espelho nas outras linhas do certificado e o
// _patchProduct com a api falsa), confere a célula Validade no template e
// compila o SFC inteiro.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const pega = (nome) => {
  const node = source.statements.find((c) =>
    (ts.isFunctionDeclaration(c) && c.name?.text === nome)
    || (ts.isVariableStatement(c) && c.declarationList.declarations.some((d) => d.name.getText(source) === nome)))
  assert.ok(node, `real page helper ${nome} exists`)
  return node.getText(source)
}
const nomes = [
  'certTitulo', 'garantiaDoCertificado', 'garantiaTitulo', 'validadeTitulo', 'lerDataValidade',
  'espelharCertificado', 'GARANTIA_MESES_MAX', 'lerGarantiaMeses', '_patchProduct',
  'VALIDADE_INCOMPLETA', 'dataIncompleta', 'GARANTIA_DO_CERTIFICADO', 'erroDoProduto', 'commitEditProduct',
]
const compiled = ts.transpileModule(nomes.map(pega).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])

function pagina({ podeEditar = true, linhas = [], resposta } = {}) {
  const chamadas = []
  const flashes = []
  const env = {
    canEditProdutos: { value: podeEditar },
    products: { value: linhas },
    grid: { value: null },
    productsErr: { value: null },
    api: async (url, opts) => {
      chamadas.push({ url, opts })
      if (resposta instanceof Error) throw resposta
      return typeof resposta === 'function' ? resposta(url, opts) : resposta
    },
    flash: (id, field) => flashes.push(`${id}:${field}`),
    recomputeCellsForProduct: () => {},
    loadGrid: async () => {},
    loadProducts: async () => {},
    editing: { value: null },
    editValue: { value: '' },
    editInputRef: { value: null },
  }
  const fns = new Function(...Object.keys(env), `${compiled.outputText}
return { certTitulo, garantiaDoCertificado, garantiaTitulo, validadeTitulo, lerDataValidade, espelharCertificado, _patchProduct, commitEditProduct, erroDoProduto, VALIDADE_INCOMPLETA, GARANTIA_DO_CERTIFICADO }`)(...Object.values(env))
  return { ...fns, env, chamadas, flashes }
}

const cert = (v = {}) => ({
  id: 'c1', produto: 'smartphone 4G', modelo: 'USM003', certificado: 'anatel',
  numero: '07197-26-18234', fim: '2028-07-30', garantia_meses: 12, ...v,
})
const produto = (v = {}) => ({ id: 'p1', sku: 'dg052', name: 'uranyx A17', garantia_meses: 12, certificacao: cert(), ...v })

async function run() {
  // 1) títulos: a garantia que vem do certificado avisa que vale para todos.
  {
    const s = pagina()
    assert.equal(s.garantiaTitulo(produto({ certificacao: null })), 'Garantia de 12 meses')
    assert.equal(s.garantiaTitulo(produto({ certificacao: null, garantia_meses: 1 })), 'Garantia de 1 mês')
    assert.equal(s.garantiaTitulo(produto({ certificacao: null, garantia_meses: 0 })), 'Sem garantia (0 = sem garantia)')
    assert.equal(s.garantiaTitulo(produto({ certificacao: null, garantia_meses: null })), 'Prazo da garantia não cadastrado')
    // Certificado sem garantia: o prazo é do produto, o title não fala do certificado.
    assert.equal(s.garantiaTitulo(produto({ certificacao: cert({ garantia_meses: null }) })), 'Garantia de 12 meses')
    assert.equal(s.garantiaDoCertificado(produto({ certificacao: cert({ garantia_meses: null }) })), false)
    assert.equal(s.garantiaDoCertificado(produto({ certificacao: cert({ garantia_meses: 0 }) })), true, '0 também vem do certificado')
    assert.equal(
      s.garantiaTitulo(produto()),
      'Garantia de 12 meses — vem do certificado USM003: vale para todos os produtos dele.',
    )
    assert.match(s.garantiaTitulo(produto({ certificacao: cert({ modelo: null }) })), /vem do certificado smartphone 4G:/)
    // Validade: sem certificado não edita; com certificado avisa que vale para todos.
    assert.match(s.validadeTitulo(produto({ certificacao: null })), /escolha o certificado na coluna Modelo/)
    assert.match(s.validadeTitulo(produto()), /^Da Certificações: .*Válido até 30\/07\/2028\..* Clique para mudar a data \(o Fim do certificado\): vale para todos os produtos dele\.$/)
    const leitor = pagina({ podeEditar: false })
    assert.equal(leitor.validadeTitulo(produto()), leitor.certTitulo(cert()), 'quem só vê não recebe o "clique"')
  }

  // 2) a data digitada.
  {
    const { lerDataValidade } = pagina()
    assert.equal(lerDataValidade(''), null, 'vazio apaga a validade')
    assert.equal(lerDataValidade('  '), null)
    assert.equal(lerDataValidade('2030-01-31'), '2030-01-31')
    assert.equal(lerDataValidade(' 2028-02-29 '), '2028-02-29', 'bissexto')
    for (const ruim of ['2026-02-30', '2027-02-29', '2026-13-01', '31/01/2030', '2030-1-31', 'amanhã']) {
      assert.equal(lerDataValidade(ruim), undefined, `"${ruim}" não envia`)
    }
  }

  // 3) espelho nas outras linhas do MESMO certificado.
  {
    const outra = produto({ id: 'p2', garantia_meses: 6 })
    const deOutro = produto({ id: 'p3', garantia_meses: 3, certificacao: cert({ id: 'c2', fim: '2027-01-01' }) })
    const semCert = produto({ id: 'p4', garantia_meses: null, certificacao: null })
    const editada = produto()
    const s = pagina({ linhas: [editada, outra, deOutro, semCert] })
    const novo = produto({ garantia_meses: 24, certificacao: cert({ garantia_meses: 24, fim: '2030-01-31' }) })
    s.espelharCertificado(novo, false)
    assert.equal(outra.certificacao.fim, '2030-01-31', 'a validade nova aparece nas outras linhas')
    assert.equal(outra.garantia_meses, 6, 'sem prazoTambem o prazo da linha não muda')
    s.espelharCertificado(novo, true)
    assert.equal(outra.garantia_meses, 24)
    assert.notEqual(outra.certificacao, novo.certificacao, 'cópia, não o mesmo objeto')
    assert.deepEqual([deOutro.garantia_meses, deOutro.certificacao.fim], [3, '2027-01-01'])
    assert.equal(semCert.garantia_meses, null)
    assert.equal(editada.garantia_meses, 12, 'a própria linha vem da resposta, não do espelho')
  }

  // 4) _patchProduct da Validade: manda certificado_fim, pinta o Fim do
  // certificado (não põe campo novo na linha) e espelha nas outras.
  {
    const editada = produto()
    const outra = produto({ id: 'p2' })
    const s = pagina({
      linhas: [editada, outra],
      resposta: produto({ certificacao: cert({ fim: '2030-01-31' }) }),
    })
    const indo = s._patchProduct('p1', 'certificado_fim', '2030-01-31')
    assert.equal(editada.certificacao.fim, '2030-01-31', 'otimista')
    assert.equal('certificado_fim' in editada, false, 'a validade não vira campo da linha')
    await indo
    assert.deepEqual(s.chamadas.map((c) => [c.url, c.opts.method, c.opts.body]), [
      ['/api/pricing/products/p1', 'PATCH', { certificado_fim: '2030-01-31' }],
    ])
    assert.equal(outra.certificacao.fim, '2030-01-31')
    assert.deepEqual(s.flashes, ['p1:certificado_fim'])
    // Mesma data: não manda nada. Data inválida: avisa e não manda.
    await s._patchProduct('p1', 'certificado_fim', '2030-01-31')
    await s._patchProduct('p1', 'certificado_fim', '2026-02-30')
    assert.equal(s.chamadas.length, 1)
    assert.match(s.env.productsErr.value, /^Validade: data inválida/)
    // Vazio apaga a data (null) e limpa o aviso.
    await s._patchProduct('p1', 'certificado_fim', '')
    assert.deepEqual(s.chamadas[1].opts.body, { certificado_fim: null })
    assert.equal(s.env.productsErr.value, null)
    // Produto sem certificado: não manda.
    const solto = produto({ id: 'p9', certificacao: null })
    s.env.products.value.push(solto)
    await s._patchProduct('p9', 'certificado_fim', '2030-01-31')
    assert.equal(s.chamadas.length, 2)
  }

  // 5) _patchProduct da Garantia: com certificado que tem garantia, as outras
  // linhas dele recebem o prazo novo; sem garantia no certificado, só a linha.
  {
    const editada = produto()
    const outra = produto({ id: 'p2' })
    const s = pagina({
      linhas: [editada, outra],
      resposta: produto({ garantia_meses: 24, certificacao: cert({ garantia_meses: 24 }) }),
    })
    await s._patchProduct('p1', 'garantia_meses', '24')
    assert.deepEqual(s.chamadas[0].opts.body, { garantia_meses: 24 })
    assert.deepEqual([editada.garantia_meses, outra.garantia_meses], [24, 24])
    assert.equal(outra.certificacao.garantia_meses, 24)

    const a = produto({ certificacao: cert({ garantia_meses: null }) })
    const b = produto({ id: 'p2', garantia_meses: 6, certificacao: cert({ garantia_meses: null }) })
    const s2 = pagina({
      linhas: [a, b],
      resposta: produto({ garantia_meses: 3, certificacao: cert({ garantia_meses: null }) }),
    })
    await s2._patchProduct('p1', 'garantia_meses', '3')
    assert.deepEqual([a.garantia_meses, b.garantia_meses], [3, 6], 'certificado sem garantia: só a linha')
  }

  // 5b) Revisão de 06/11/2026: a tela decide pelo que o SERVIDOR devolveu.
  {
    // A tela abriu com o certificado SEM garantia; outra aba pôs 24 na
    // Certificações; o servidor passou 24 a todos → as outras linhas também.
    const p1 = produto({ garantia_meses: 6, certificacao: cert({ garantia_meses: null }) })
    const p2 = produto({ id: 'p2', garantia_meses: 6, certificacao: cert({ garantia_meses: null }) })
    const s = pagina({
      linhas: [p1, p2],
      resposta: produto({ garantia_meses: 24, certificacao: cert({ garantia_meses: 24 }) }),
    })
    await s._patchProduct('p1', 'garantia_meses', '24')
    assert.deepEqual([p1.garantia_meses, p2.garantia_meses], [24, 24], 'p2 acompanha sem recarregar')
    assert.equal(p2.certificacao.garantia_meses, 24)
    assert.match(s.garantiaTitulo(p2), /vem do certificado USM003/)
    // O contrário: a tela achava que o certificado tinha garantia, mas ela foi
    // apagada na Certificações — o servidor gravou só na linha.
    const q1 = produto({ garantia_meses: 12 })
    const q2 = produto({ id: 'p2', garantia_meses: 12 })
    const s2 = pagina({
      linhas: [q1, q2],
      resposta: produto({ garantia_meses: 3, certificacao: cert({ garantia_meses: null }) }),
    })
    await s2._patchProduct('p1', 'garantia_meses', '3')
    assert.deepEqual([q1.garantia_meses, q2.garantia_meses], [3, 12], 'só a linha editada')
    assert.equal(q2.certificacao.garantia_meses, null, 'e a outra linha sabe que o certificado não define mais')
    assert.doesNotMatch(s2.garantiaTitulo(q2), /vem do certificado/)
  }

  // 5c) Apagar a Garantia que vem do certificado: não manda, avisa.
  {
    const editada = produto()
    const s = pagina({ linhas: [editada], resposta: produto() })
    await s._patchProduct('p1', 'garantia_meses', '')
    assert.equal(s.chamadas.length, 0)
    assert.equal(s.env.productsErr.value, s.GARANTIA_DO_CERTIFICADO)
    assert.match(s.GARANTIA_DO_CERTIFICADO, /^Garantia: .*apague na Certificações \(ou use 0 = sem garantia\)$/)
    assert.equal(editada.garantia_meses, 12, 'nada mudou na linha')
    // 0 = sem garantia continua indo (vale para todos).
    await s._patchProduct('p1', 'garantia_meses', '0')
    assert.deepEqual(s.chamadas.map((c) => c.opts.body), [{ garantia_meses: 0 }])
    assert.equal(s.env.productsErr.value, null, 'o valor certo limpa o aviso')
    // Certificado sem garantia: apagar continua valendo para a linha.
    const solta = produto({ id: 'p3', certificacao: cert({ garantia_meses: null }) })
    s.env.products.value.push(solta)
    await s._patchProduct('p3', 'garantia_meses', '')
    assert.deepEqual(s.chamadas[1].opts.body, { garantia_meses: null })
    // O 422 do servidor (a tela estava velha) vira frase; o resto, o código.
    const s3 = pagina({
      linhas: [produto({ certificacao: cert({ garantia_meses: null }) })],
      resposta: Object.assign(new Error('422'), { data: { detail: { code: 'garantia_do_certificado' } } }),
    })
    await s3._patchProduct('p1', 'garantia_meses', '')
    assert.equal(s3.env.productsErr.value, s3.GARANTIA_DO_CERTIFICADO)
    assert.match(s3.erroDoProduto('sem_certificado'), /^Validade: o produto não tem certificado/)
    assert.equal(s3.erroDoProduto('sku_exists'), 'sku_exists')
    assert.equal(s3.erroDoProduto(undefined), 'save_failed')
  }

  // 5d) Validade com data incompleta ou que não existe: o <input type="date">
  // entrega value "" com validity.badInput — não pode virar "apagar a data".
  {
    const editada = produto()
    const s = pagina({ linhas: [editada], resposta: produto({ certificacao: cert({ fim: null }) }) })
    const editar = (valor, badInput) => {
      s.env.editing.value = { id: 'p1', field: 'certificado_fim' }
      s.env.editValue.value = valor
      s.env.editInputRef.value = { type: 'date', validity: { badInput } }
    }
    editar('', true)  // ano apagado pela metade, ou 30/02/2026
    s.commitEditProduct()
    await new Promise(setImmediate)
    assert.equal(s.chamadas.length, 0, 'não manda certificado_fim: null')
    assert.equal(s.env.productsErr.value, s.VALIDADE_INCOMPLETA)
    assert.equal(s.env.editing.value, null, 'a edição fecha')
    assert.equal(editada.certificacao.fim, '2028-07-30', 'a data continua')
    // Campo apagado de verdade (sem badInput): apaga a validade.
    editar('', false)
    s.commitEditProduct()
    await new Promise(setImmediate)
    assert.deepEqual(s.chamadas.map((c) => c.opts.body), [{ certificado_fim: null }])
    assert.equal(s.env.productsErr.value, null)
    // Data boa: vai.
    editar('2031-05-01', false)
    s.commitEditProduct()
    await new Promise(setImmediate)
    assert.deepEqual(s.chamadas[1].opts.body, { certificado_fim: '2031-05-01' })
    // badInput de OUTRO campo (não é a Validade) não muda nada.
    s.env.editing.value = { id: 'p1', field: 'description' }
    s.env.editValue.value = 'nova'
    s.env.editInputRef.value = { type: 'text', validity: { badInput: false } }
    s.commitEditProduct()
    await new Promise(setImmediate)
    assert.deepEqual(s.chamadas[2].opts.body, { description: 'nova' })
  }

  // 6) startEditProduct: Validade só abre com certificado e começa na data Fim.
  const start = pega('startEditProduct')
  assert.match(start, /if \(field === 'certificado_fim' && !p\.certificacao\) return/)
  assert.match(start, /field === 'certificado_fim' \? p\.certificacao\?\.fim : \(p as any\)\[field\]/)

  // 7) template: cabeçalho e célula da Validade.
  const tpl = descriptor.template.content
  const iCab = tpl.indexOf('data-col-cert-validade')
  assert.ok(iCab > 0, 'cabeçalho marcado')
  const th = tpl.slice(tpl.lastIndexOf('<th', iCab), tpl.indexOf('</th>', iCab))
  assert.match(th, /Clique para mudar a data: grava no Fim do certificado e vale para todos os produtos dele\./)
  const corpo = tpl.slice(tpl.indexOf('<!-- data rows -->'))
  const iCel = corpo.indexOf('data-cert-validade\n')
  assert.ok(iCel > 0, 'célula da linha existe')
  const celula = corpo.slice(corpo.lastIndexOf('<td', iCel), corpo.indexOf('</td>', iCel))
  assert.match(celula, /:title="validadeTitulo\(p\)"/)
  assert.match(celula, /@click="canEditProdutos && p\.certificacao && !isEditing\(p\.id, 'certificado_fim'\) && startEditProduct\(p, 'certificado_fim'\)"/)
  assert.match(celula, /'cursor-pointer': canEditProdutos && !!p\.certificacao/)
  assert.match(celula, /v-if="isEditing\(p\.id, 'certificado_fim'\)"[\s\S]*?type="date"/)
  assert.match(celula, /@blur="commitEditProduct"/)
  assert.match(celula, /@keydown\.escape\.prevent="cancelEdit"/)
  assert.match(celula, /<template v-else>\{\{ certValidade\(p\.certificacao\) \}\}<\/template>/, 'mostra os dias como antes')
  // A Garantia usa o title novo.
  const gar = corpo.slice(corpo.indexOf('<!-- Garantia (meses), 06/11/2026'), corpo.indexOf('<!-- Fotos:'))
  assert.match(gar, /:title="garantiaTitulo\(p\)"/)
  // O tipo do certificado traz a garantia.
  assert.match(setup, /type CertificacaoDoProduto = \{[\s\S]*?garantia_meses\?: number \| null[\s\S]*?\}/)

  // 8) o SFC inteiro compila.
  const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
  const out = compileTemplate({
    source: tpl, filename, id: 'pricing-tab',
    compilerOptions: { isTS: true, bindingMetadata: script.bindings },
  })
  assert.deepEqual(out.errors, [], 'template compila')
  for (const b of ['validadeTitulo', 'garantiaTitulo']) assert.ok(script.bindings[b], `${b} exposto ao template`)

  console.log('pricing-certificado-garantia: ok')
}
run().catch((e) => { console.error(e); process.exitCode = 1 })
