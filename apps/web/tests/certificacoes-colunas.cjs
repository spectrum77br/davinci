// Run from apps/web: node tests/certificacoes-colunas.cjs
// M-68 (26/11/2026): colunas da aba Importação › Certificações.
// - Início sai da tela (o dado continua guardado); Fim vira Validade.
// - Certificado: só o nome, centralizado (Inmetro, Anatel, Anvisa, Isento;
//   vazio = "—" cinza); clicar abre as caixas Inmetro / Anatel / Anvisa /
//   Isento, uma por vez; desmarcar tudo deixa vazio; grava em minúsculas.
// - Situação oficial: só órgão + situação; manual = em branco; cadastro
//   editado = a situação da fonte; sem "Ver" (clicar no texto abre a janela);
//   "não localizado" e "observações" viram selos curtos com o texto no title.
// Usa o page()/render() de certificacoes-anatel.cjs (o <script setup> de
// verdade com api falsa).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { page, row, source, render, settle } = require('./certificacoes-anatel.cjs')

const template = fs.readFileSync(path.join(__dirname, '../components/CertificacoesPainel.vue'), 'utf8')
  .split('<template>')[1]
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')

// Os <th> da tabela, em ordem: [texto, classe].
function cabecalhos(html) {
  const thead = html.slice(html.indexOf('<thead>'), html.indexOf('</thead>'))
  return [...thead.matchAll(/<th class="([^"]*)"[^>]*>([^<]*)<\/th>/g)].map((m) => [m[2].trim(), m[1]])
}

// A célula do Certificado de cada linha da tabela (o texto que aparece).
function certificados(html) {
  const tabela = html.slice(html.indexOf('<tbody>'))
  return [...tabela.matchAll(/class="cert-cell"[^>]*data-cert-certificado[^>]*>(<span[^>]*>[^<]*<\/span>)/g)].map((m) => m[1])
}

async function run() {
  // ---------- cabeçalho: sem Início, Validade no lugar do Fim, Certificado centralizado
  {
    const { state: s } = page()
    s.rows.value = [row()]
    const html = semComentarios(await render(s))
    // M-69: o SKU entrou entre Produto e Modelo.
    assert.deepEqual(cabecalhos(html).map(([t]) => t), [
      'Produto', 'SKU', 'Modelo', 'Nome comercial', 'Certificado', 'Número', 'Valor', 'Validade',
      'Garantia (meses)', 'Situação oficial', 'PDF do certificado', '',
    ])
    assert.equal(cabecalhos(html)[4][1], 'text-center', 'título do Certificado centralizado')
    assert.equal(s.totalColunas.value, 12, '11 colunas + lixeira')
    assert.doesNotMatch(html, /<th[^>]*>\s*(Início|Fim)\s*<\/th>/)
    assert.doesNotMatch(html, /type="date"[^>]*value="2026-07-30"/, 'a data de Início não aparece na tabela')
    assert.match(html, /type="date"[^>]*value="2028-07-30"/, 'a Validade continua editável')
    assert.doesNotMatch(html, /<select/, 'Certificado sem a setinha (não é mais caixa de seleção)')
    // O dado do Início continua na linha (só saiu da tela).
    assert.equal(s.rows.value[0].inicio, '2026-07-30')
    // Lista vazia: o colspan acompanha (11 + lixeira).
    s.rows.value = []
    assert.match(await render(s), /<td colspan="12"[^>]*>\s*Nenhuma certificação/)
  }

  // ---------- Certificado: só o nome, centralizado; vazio = "—" cinza
  {
    const { state: s } = page()
    s.rows.value = [
      row({ id: 'a', certificado: 'inmetro' }),
      row({ id: 'b', certificado: 'anatel' }),
      row({ id: 'c', certificado: 'anvisa' }),
      row({ id: 'd', certificado: 'isento' }),
      row({ id: 'e', certificado: '' }),
      row({ id: 'f', certificado: null }),
    ]
    const html = semComentarios(await render(s))
    assert.deepEqual(certificados(html), [
      '<span>Inmetro</span>', '<span>Anatel</span>', '<span>Anvisa</span>', '<span>Isento</span>',
      '<span class="text-muted-foreground">—</span>', '<span class="text-muted-foreground">—</span>',
    ])
    assert.equal((html.match(/<td class="text-center"><div><div as-child><button type="button" class="cert-cell"/g) || []).length, 6,
      'a célula é centralizada e o nome é um botão para quem edita')
    assert.equal(s.rotuloCertificado(' ANVISA '), 'Anvisa')
    assert.equal(s.rotuloCertificado(''), '')
    // O menu: caixas nesta ordem, só a do valor atual marcada.
    const ini = html.indexOf('data-menu-certificado')
    const menu = html.slice(ini, html.indexOf('</div>', ini))
    assert.deepEqual(
      [...menu.matchAll(/<input[^>]*data-campo="certificado-(\w+)"[^>]*>\s*(\w+)/g)].map((m) => [m[1], m[2], /\schecked(\s|>|=)/.test(m[0])]),
      [['inmetro', 'Inmetro', true], ['anatel', 'Anatel', false], ['anvisa', 'Anvisa', false], ['isento', 'Isento', false]],
    )
    // Cartão do celular também mostra o nome.
    assert.match(html, /<span class="rounded-md bg-muted px-2 py-1 text-xs">Anvisa<\/span>/)
    assert.doesNotMatch(html, /<dt class="text-muted-foreground">Início<\/dt>/, 'Início saiu do cartão do celular')
  }

  // ---------- Certificado: uma caixa por vez; desmarcar = vazio; grava em minúsculas
  {
    const { state: s, calls } = page()
    const current = row({ certificado: 'inmetro' })
    s.rows.value = [current]
    s.abrirMenuCertificado(current, true)
    assert.equal(s.menuCertificado.value, current.id)
    s.escolherCertificado(current, 'anvisa', true)
    assert.equal(current.certificado, 'anvisa', 'marcar outra troca (só uma fica marcada)')
    assert.equal(s.menuCertificado.value, null, 'o menu fecha ao escolher')
    assert.ok(current.campos_manuais.includes('certificado'))
    let loading = s.load()
    assert.equal(calls[0].options.method, 'PATCH')
    assert.deepEqual(calls[0].options.body, { certificado: 'anvisa' })
    calls[0].resolve()
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(null)
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(null)
    await loading
    // Desmarcar a marcada deixa vazio ('' no mesmo campo).
    s.escolherCertificado(current, 'anvisa', false)
    assert.equal(current.certificado, '')
    calls.length = 0
    loading = s.load()
    assert.deepEqual(calls[0].options.body, { certificado: '' })
    calls[0].resolve()
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(null)
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(null)
    await loading
    // Escolher o que já está: nada a salvar.
    current.certificado = 'isento'
    calls.length = 0
    s.escolherCertificado(current, 'isento', true)
    s.escolherCertificado(current, 'anatel', false)
    assert.equal(current.certificado, 'isento')
    loading = s.load()
    await settle()
    assert.equal(calls.length, 3, 'nenhum PATCH, só a releitura')
    assert.ok(!calls.some((c) => c.options?.method === 'PATCH'))
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(null)
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(null)
    await loading
  }

  // ---------- Certificado: quem só vê não muda e não tem menu
  {
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true } } } })
    const current = row({ certificado: 'anatel' })
    s.rows.value = [current]
    s.escolherCertificado(current, 'anvisa', true)
    assert.equal(current.certificado, 'anatel')
    assert.equal(calls.length, 0)
    const html = semComentarios(await render(s))
    assert.match(html, /<td class="text-center"><span class="cert-cell" data-cert-certificado><span>Anatel<\/span><\/span><\/td>/)
    assert.doesNotMatch(html, /data-menu-certificado/)
  }

  // ---------- Situação oficial: só órgão + situação, sem "Ver"
  {
    const { state: s } = page()
    const anatel = row({ id: 'a', anatel_numero: '07197-26-18234', anatel_dados: source(), anatel_encontrado: true })
    const analise = row({ id: 'b', anatel_numero: 'X2', anatel_dados: source({ situacao_requerimento: 'Em Análise - RE' }), anatel_encontrado: true })
    const manual = row({ id: 'c', produto: 'bateria a17' })
    const editado = row({
      id: 'd', anatel_numero: 'X3', anatel_dados: source(), anatel_encontrado: true,
      campos_manuais: ['numero'], numero: 'Digitado à mão',
    })
    const semSituacao = row({ id: 'e', anatel_numero: 'X4', anatel_dados: source({ situacao_requerimento: null }), anatel_encontrado: true })
    const naoLocalizado = row({ id: 'f', anatel_numero: 'X5', anatel_dados: source(), anatel_encontrado: false })
    const comObservacoes = row({ id: 'g', anatel_numero: 'X6', anatel_dados: source({ alertas: ['Modelo repetido.'] }), anatel_encontrado: true })
    const inmetro = row({
      id: 'h', certificado: 'inmetro', inmetro_chave: 'K', inmetro_encontrado: true,
      inmetro_dados: { situacao_certificado: 'Ativo', alertas: [], campos_confirmados: [] },
    })
    s.rows.value = [anatel, analise, manual, editado, semSituacao, naoLocalizado, comObservacoes, inmetro]
    assert.deepEqual(s.rows.value.map((r) => s.officialSituation(r)), [
      'Anatel · Homologação Emitida', 'Anatel · Em Análise - RE', '', 'Anatel · Homologação Emitida', '',
      'Anatel · Homologação Emitida', 'Anatel · Homologação Emitida', 'Inmetro · Ativo',
    ])
    // Cores de hoje: verde emitida/ativo, âmbar em análise, cinza editado/não localizado.
    assert.match(s.officialSituationClass(anatel), /emerald/)
    assert.match(s.officialSituationClass(analise), /amber/)
    assert.match(s.officialSituationClass(inmetro), /emerald/)
    assert.match(s.officialSituationClass(editado), /muted/)
    assert.match(s.officialSituationClass(naoLocalizado), /muted/)
    assert.equal(s.officialSituationTitle(anatel), 'Clique para ver os dados de Anatel.')
    assert.match(s.officialSituationTitle(editado), /^Cadastro editado à mão: esta é a situação da fonte \(Anatel\)\./)
    const html = semComentarios(await render(s))
    assert.doesNotMatch(html, /Manual · sem confirmação automática|Cadastro editado · consultar fonte|Situação não informada na fonte/)
    assert.doesNotMatch(html, />\s*Ver\s*<\/button>/, 'o link "Ver" saiu')
    const celulas = [...html.matchAll(/<td class="official-status-cell">([\s\S]*?)<\/td>/g)].map((m) => m[1])
    assert.equal(celulas.length, 8)
    const textoDa = (c) => c.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
    assert.deepEqual(celulas.map(textoDa), [
      'Anatel · Homologação Emitida', 'Anatel · Em Análise - RE', '', 'Anatel · Homologação Emitida', '',
      'Anatel · Homologação Emitida não localizado', 'Anatel · Homologação Emitida observações', 'Inmetro · Ativo',
    ])
    // O texto é um botão (mãozinha) que abre a janela dos dados da fonte.
    const botao = celulas[0].match(/<button[^>]*data-situacao-oficial[^>]*>/)[0]
    assert.match(botao, /class="situacao-link text-emerald-700/)
    assert.match(botao, /aria-label="Ver dados de Anatel de Telefone"/)
    assert.match(template, /<button v-if="officialSituation\(row\)"[^>]*class="situacao-link"[\s\S]*?@click="openDetails\(row\)">\{\{ officialSituation\(row\) \}\}<\/button>/)
    assert.match(template, /\.situacao-link \{\s*cursor: pointer;/, 'cursor de mãozinha')
    // Selos curtos com o texto completo no title (e também abrem a janela).
    assert.match(celulas[5], /<button type="button" class="selo-aviso" data-selo-aviso="nao-localizado" title="Não localizado na última consulta\. Os dados são da consulta anterior\.">não localizado<\/button>/)
    assert.match(celulas[6], /<button type="button" class="selo-aviso" data-selo-aviso="observacoes" title="Confira as observações da fonte\.">observações<\/button>/)
    assert.match(template, /data-selo-aviso="nao-localizado"[\s\S]*?@click="openDetails\(row\)"/)
    assert.match(template, /\.selo-aviso \{[\s\S]*?background: rgb\(254 243 199\);/, 'selo laranja-claro')
    // Linha manual: célula em branco, nada clicável.
    assert.doesNotMatch(celulas[2], /<button/)
    // Celular: sem situação, a linha "Situação oficial" do cartão não aparece.
    const cartoes = html.slice(html.indexOf('aria-label="Certificações por produto"'), html.indexOf('</section>'))
    assert.equal((cartoes.match(/<dt class="text-muted-foreground">Situação oficial<\/dt>/g) || []).length, 6)
  }

  // ---------- nenhum texto da tela fala em "Fim"
  {
    const { state: s } = page()
    s.rows.value = [row()]
    const html = semComentarios(await render(s))
    assert.doesNotMatch(html.replace(/<dialog[\s\S]*<\/dialog>/, ''), /\bFim\b/)
  }

  console.log('PASS: M-68 — Início fora, Validade, Certificado com caixas (Anvisa) e Situação oficial curta')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
