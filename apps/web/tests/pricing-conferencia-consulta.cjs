// Run from apps/web: node tests/pricing-conferencia-consulta.cjs
// Tabela de Preços (M-7, 18/11/2026): campo "Reposição [N] dias" só do
// administrador; "Conferir anúncios" de todos que veem a Tabela, com a data e
// o andamento no balão; a tela consulta a conferência a cada 30 s e, quando
// uma termina (de qualquer pessoa), relê os (!) e os cartões sozinha.
// Executa as funções reais da página, sem API nem DOM.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const tpl = descriptor.template.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return n.getText(source)
}
const declaracao = (nome) => {
  const n = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(n, `${nome} existe na página`)
  return n
}

// ---------- 1) o template: quem vê o quê na barra
// A tag de abertura que contém o atributo (do "<tag" anterior até o ">").
const tagCom = (atributo, tag) => {
  const i = tpl.indexOf(atributo)
  assert.ok(i > 0, `${atributo} no template`)
  const ini = tpl.lastIndexOf(`<${tag}`, i)
  return tpl.slice(ini, tpl.indexOf('>', i) + 1)
}
assert.match(tagCom('data-prazo-reposicao', 'label'), /v-if="isAdmin"/, 'Reposição [N] dias só do administrador')
// M-14 (18/11/2026): o "Conferir anúncios" virou a linha Conferir do
// Atualizar ▾; aparece para quem o servidor deixa conferir (todos desde o M-7).
const linhaConferir = tagCom('data-campo="atualizar-conferir"', 'label')
assert.match(linhaConferir, /v-if="podeConferir"/, 'Conferir segue o pode_conferir do servidor')
assert.doesNotMatch(linhaConferir, /isAdmin/, 'Conferir para todos')
// A Margem continua só do administrador (o PopoverRoot em volta do botão).
const iMargem = tpl.indexOf('data-acao="margem"')
assert.match(tpl.slice(tpl.lastIndexOf('<PopoverRoot', iMargem), iMargem), /^<PopoverRoot v-if="isAdmin"/)
// A coluna Reposição da grade não mudou: segue o Layout → Estoque, sem isAdmin.
for (const coluna of [...tpl.matchAll(/data-coluna="reposicao"/g)]) {
  const ini = Math.max(tpl.lastIndexOf('<th', coluna.index), tpl.lastIndexOf('<td', coluna.index))
  assert.doesNotMatch(tpl.slice(ini, coluna.index), /isAdmin/, 'coluna Reposição sem trava de administrador')
}
// O clique não pergunta mais se é administrador.
assert.doesNotMatch(funcao('conferirAnuncios'), /isAdmin/)
console.log('pricing conferência M-7 template ok')

// ---------- 2) balão do botão para quem não é administrador
// O servidor manda o job sem o resumo (result {}): a data e o andamento, sem
// a lista de contas que não leram.
const st = declaracao('tituloConferir')
const ini = st.declarationList.declarations[0].initializer
const tituloDe = (conf) => new Function(
  'conferencia', 'conferenciaEstado', 'conferenciaRodando', 'releitura', 'fmtQuando', 'textoErroConferencia', 'ERROS_CONFERENCIA',
  `${js(`const __f = ${ini.arguments[0].getText(source)}`)}\nreturn __f()`,
)(
  { value: conf },
  { value: 'ok' },
  { value: ['pending', 'running'].includes(conf?.job?.status) },
  { value: 'ok' },
  () => '18/11, 10:00',
  (c) => c ?? '',
  {},
)
const jobFeito = { id: 'J1', status: 'succeeded', total: 5, processed: 5, created_at: 'x', finished_at: 'y', error: null, result: {} }
const t1 = tituloDe({ job: jobFeito, ultimo_concluido: 'J1', celulas: [] })
assert.match(t1, /Última conferência: 18\/11, 10:00\./)
assert.doesNotMatch(t1, /não leram/)
const t2 = tituloDe({ job: { ...jobFeito, id: 'J2', status: 'running', processed: 2, finished_at: null }, ultimo_concluido: 'J1', celulas: [] })
assert.match(t2, /Lendo os anúncios: 2 de 5 contas\./)
console.log('pricing conferência M-7 balão ok')

// ---------- 3) a consulta de 30 s
assert.match(js(declaracao('INTERVALO_CONSULTA').getText(source)), /INTERVALO_CONSULTA = 30000/)
const corpo = [
  'let timerConferencia = null',
  'let desmontado = false',
  'let seqConferencia = 0',
  'const INTERVALO_CONSULTA = 30000',
  // import.meta (só no Nuxt): aqui é sempre o navegador.
  js(funcao('agendarConferencia').replaceAll('import.meta.client', 'true')),
  js(funcao('consultarConferencia')),
  `return {
    agendarConferencia, consultarConferencia,
    get timer() { return timerConferencia },
    desmontar() { desmontado = true },
    leituraInteira() { seqConferencia++ },
  }`,
].join('\n')

function montar({ admin = false, aba = 'tabela', conf = null } = {}) {
  const timers = new Map()
  let proximo = 0
  const chamadas = { api: [], inteira: 0, acompanhar: 0 }
  const respostas = []
  const conferencia = { value: conf }
  const env = {
    conferencia,
    conferenciaRodando: { get value() { return ['pending', 'running'].includes(conferencia.value?.job?.status) } },
    isAdmin: { value: admin },
    tab: { value: aba },
    department: { value: 'celular' },
    releitura: { value: 'ok' },
    api: async (url) => {
      chamadas.api.push(url)
      const r = respostas.shift()
      if (r instanceof Error) throw r
      return typeof r === 'function' ? r() : r
    },
    carregarConferencia: async () => { chamadas.inteira++ },
    acompanharConferencia: async () => { chamadas.acompanhar++ },
    setTimeout: (fn, ms) => { const id = ++proximo; timers.set(id, { fn, ms }); return id },
    clearTimeout: (id) => { timers.delete(id) },
  }
  const nomes = Object.keys(env)
  const f = new Function(...nomes, corpo)(...nomes.map((n) => env[n]))
  // O timer que está de pé (no máximo um: agendar sempre limpa o anterior).
  const dePe = () => {
    assert.ok(timers.size <= 1, 'um timer só')
    return [...timers.values()][0] ?? null
  }
  return { f, env, chamadas, respostas, timers, dePe }
}

const job = (id, status, extra = {}) =>
  ({ id, status, total: 5, processed: status === 'running' ? 2 : 5, created_at: 'c', finished_at: null, error: null, result: {}, ...extra })
const resposta = (j, ultimo = 'J1') => ({ job: j, ultimo_concluido: ultimo, celulas: [], pode_conferir: true })

;(async () => {
  // Parada, aba Tabela: consulta a cada 30 s (administrador ou não).
  for (const admin of [false, true]) {
    const m = montar({ admin, conf: resposta(job('J1', 'succeeded')) })
    m.f.agendarConferencia()
    assert.equal(m.dePe().ms, 30000)
  }
  // Fora da aba Tabela (ou desmontada): nenhum timer.
  for (const aba of ['contas', 'produtos', 'faturamento']) {
    const m = montar({ aba, conf: resposta(job('J1', 'succeeded')) })
    m.f.agendarConferencia()
    assert.equal(m.dePe(), null, `sem timer na aba ${aba}`)
  }
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.f.agendarConferencia()
    m.f.desmontar()
    m.f.agendarConferencia()
    assert.equal(m.dePe(), null, 'desmontada: o timer antigo sai e nenhum novo entra')
  }
  // Rodando: o administrador acompanha a cada 4 s; os outros, a consulta de 30 s.
  {
    const m = montar({ admin: true, conf: resposta(job('J2', 'running')) })
    m.f.agendarConferencia()
    assert.equal(m.dePe().ms, 4000)
    await m.dePe().fn()
    assert.equal(m.chamadas.acompanhar, 1)
    const n = montar({ admin: false, conf: resposta(job('J2', 'running')) })
    n.f.agendarConferencia()
    assert.equal(n.dePe().ms, 30000)
  }

  // Nada mudou: consulta leve (sem as células), não relê nada e agenda de novo.
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(resposta(job('J1', 'succeeded')))
    await m.f.consultarConferencia()
    assert.deepEqual(m.chamadas.api, ['/api/pricing/conferencia?department=celular&celulas=false'])
    assert.equal(m.chamadas.inteira, 0, 'não relê as células à toa')
    assert.equal(m.dePe().ms, 30000)
  }
  // Outra pessoa iniciou: o andamento aparece no botão, as células não são relidas.
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(resposta(job('J2', 'running')))
    await m.f.consultarConferencia()
    assert.equal(m.chamadas.inteira, 0)
    assert.equal(m.env.conferencia.value.job.id, 'J2')
    assert.equal(m.env.conferencia.value.job.processed, 2)
    assert.equal(m.dePe().ms, 30000)
    // ...e o andamento segue a cada consulta.
    m.respostas.push(resposta(job('J2', 'running', { processed: 4 })))
    await m.dePe().fn()
    assert.equal(m.env.conferencia.value.job.processed, 4)
    assert.equal(m.chamadas.inteira, 0)
    // Terminou: relê a conferência inteira (os (!) e os cartões), uma vez.
    m.respostas.push(resposta(job('J2', 'succeeded'), 'J2'))
    await m.dePe().fn()
    assert.equal(m.chamadas.inteira, 1)
  }
  // O administrador vê a de outra pessoa começar e passa a acompanhar a cada 4 s.
  {
    const m = montar({ admin: true, conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(resposta(job('J2', 'pending')))
    await m.f.consultarConferencia()
    assert.equal(m.dePe().ms, 4000)
  }
  // Uma inteira, de outra pessoa, começou e terminou entre duas consultas: relê.
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(resposta(job('J3', 'succeeded'), 'J3'))
    await m.f.consultarConferencia()
    assert.equal(m.chamadas.inteira, 1)
  }
  // A que rodava parou no meio (falhou): relê também (a tela marca as não lidas).
  {
    const m = montar({ conf: resposta(job('J2', 'running')) })
    m.respostas.push(resposta(job('J2', 'failed', { error: 'parou_sem_sinal' })))
    await m.f.consultarConferencia()
    assert.equal(m.chamadas.inteira, 1)
  }
  // Nunca conferido e continua assim: nada a reler.
  {
    const m = montar({ conf: resposta(null, null) })
    m.respostas.push(resposta(null, null))
    await m.f.consultarConferencia()
    assert.equal(m.chamadas.inteira, 0)
    assert.equal(m.dePe().ms, 30000)
  }
  // Sem dados (a 1ª leitura falhou) ou com a releitura falhando: relê tudo direto.
  {
    const m = montar({ conf: null })
    await m.f.consultarConferencia()
    assert.deepEqual([m.chamadas.api.length, m.chamadas.inteira], [0, 1])
    const n = montar({ conf: resposta(job('J1', 'succeeded')) })
    n.env.releitura.value = 'desistiu'
    await n.f.consultarConferencia()
    assert.deepEqual([n.chamadas.api.length, n.chamadas.inteira], [0, 1])
  }
  // Rede piscou: fica como está e tenta na próxima volta.
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(new Error('rede'))
    await m.f.consultarConferencia()
    assert.equal(m.chamadas.inteira, 0)
    assert.equal(m.dePe().ms, 30000)
  }
  // Uma leitura inteira (troca de departamento, clique) passou na frente: a
  // resposta da consulta é descartada e ela não agenda outro timer.
  {
    const m = montar({ conf: resposta(job('J1', 'succeeded')) })
    m.respostas.push(() => { m.f.leituraInteira(); return resposta(job('J9', 'succeeded'), 'J9') })
    await m.f.consultarConferencia()
    assert.deepEqual([m.chamadas.inteira, m.dePe()], [0, null])
    const n = montar({ conf: resposta(job('J1', 'succeeded')) })
    n.respostas.push(() => { n.env.conferencia.value = null; return resposta(job('J9', 'succeeded'), 'J9') })
    await n.f.consultarConferencia()
    assert.deepEqual([n.chamadas.inteira, n.dePe()], [0, null])
  }

  // Sair da aba Tabela para o timer (o watch da aba) e a página desmontada também.
  const iWatch = setup.indexOf('watch(\n  tab,')
  assert.ok(iWatch > 0, 'watch da aba')
  const watchAba = setup.slice(iWatch, setup.indexOf('{ immediate: true }', iWatch))
  assert.match(watchAba, /else if \(timerConferencia\) \{\s*\/\/[^\n]*\n\s*clearTimeout\(timerConferencia\)\s*timerConferencia = null/)
  assert.match(setup, /onBeforeUnmount\(\(\) => \{\s*desmontado = true\s*if \(timerConferencia\) clearTimeout\(timerConferencia\)/)
  console.log('pricing conferência M-7 consulta de 30 s ok')
})().catch((e) => { console.error(e); process.exit(1) })
