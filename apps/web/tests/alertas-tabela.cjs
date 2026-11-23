// Run from apps/web: node tests/alertas-tabela.cjs
// Alertas (M-19, 23/11/2026): a tela /alertas virou tabela no padrão da Tabela
// de Preços, com a barra Buscar | Equipe ▾ | Plataforma ▾ | Contas ▾ | Layout ▾
// e filtros/paginação no servidor. Executa as funções reais da página e do
// composable (sem API nem DOM) e confere a marcação da tabela e da barra.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/alertas.vue')
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
  return js(n.getText(source))
}
const constante = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  return js(st.getText(source))
}

const nomes = [
  'rotuloEquipeFiltro', 'rotuloPlataforma', 'rotuloEquipe', 'plataformaDaLinha', 'contaDaLinha',
  'quando', 'corGravidade', 'marcadas', 'leituraAtual', 'alternar', 'rotuloFiltro',
]
const f = new Function([
  constante('PLATAFORMAS'),
  ...nomes.map(funcao),
  `return { ${nomes.join(', ')} }`,
].join('\n'))()

// ---------- 1) colunas Equipe / Plataforma / Conta
assert.equal(f.rotuloEquipe({ escopo: 'adm', equipe: null }), 'ADM')
assert.equal(f.rotuloEquipe({ escopo: 'pessoal', equipe: null }), 'Pessoal')
assert.equal(f.rotuloEquipe({ escopo: 'equipe', equipe: 101 }), '101')
assert.equal(f.rotuloEquipe({ escopo: 'equipe', equipe: null }), '—')
assert.equal(f.plataformaDaLinha({ escopo: 'equipe', plataforma: 'ml' }), 'ML')
assert.equal(f.plataformaDaLinha({ escopo: 'equipe', plataforma: 'tiktok' }), 'TikTok')
assert.equal(f.plataformaDaLinha({ escopo: 'equipe', plataforma: 'nova' }), 'nova')
// ADM e Pessoal mostram "—" em Plataforma e Conta.
assert.equal(f.plataformaDaLinha({ escopo: 'adm', plataforma: 'ml' }), '—')
assert.equal(f.contaDaLinha({ escopo: 'pessoal', conta: 'atlas' }), '—')
assert.equal(f.contaDaLinha({ escopo: 'equipe', conta: 'atlas' }), 'atlas')
assert.equal(f.contaDaLinha({ escopo: 'equipe', conta: null }), '—')
assert.equal(f.rotuloEquipeFiltro('adm'), 'ADM')
assert.equal(f.rotuloEquipeFiltro('pessoal'), 'Pessoal')
assert.equal(f.rotuloEquipeFiltro('102'), '102')

// ---------- 2) Quando (dd/mm hh:mm em Brasília) e a bolinha da gravidade
assert.equal(f.quando('2026-11-23T12:05:00Z'), '23/11 09:05')
assert.equal(f.quando('2026-11-24T02:30:00Z'), '23/11 23:30')
assert.equal(f.corGravidade('error'), 'bg-red-500')
assert.equal(f.corGravidade('warning'), 'bg-orange-500')
assert.equal(f.corGravidade('info'), 'bg-blue-500')

// ---------- 3) filtros: desmarcadas vazio = sem filtro; Layout
assert.equal(f.marcadas(['adm', '101'], []), null)
assert.deepEqual(f.marcadas(['adm', '101', 'pessoal'], ['101']), ['adm', 'pessoal'])
assert.deepEqual(f.marcadas(['adm'], ['adm']), [])
assert.equal(f.leituraAtual(true, true), 'todos')
assert.equal(f.leituraAtual(true, false), 'nao_lidos')
assert.equal(f.leituraAtual(false, true), 'lidos')
assert.equal(f.leituraAtual(false, false), null)
assert.deepEqual(f.alternar(['a', 'b'], 'a', true), ['b'])
assert.deepEqual(f.alternar(['b'], 'a', false), ['b', 'a'])
assert.deepEqual(f.alternar(['a'], 'a', false), ['a'])
assert.equal(f.rotuloFiltro('Contas', ['x', 'y', 'z'], ['y']), 'Contas (2)')
assert.equal(f.rotuloFiltro('Contas', ['x', 'y'], []), 'Contas')

// ---------- 4) a query do composable (o servidor filtra e pagina)
{
  const arquivo = path.resolve(__dirname, '../composables/useAlerts.ts')
  const src = ts.createSourceFile(arquivo, fs.readFileSync(arquivo, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const n = src.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === 'montarQueryAlertas')
  assert.ok(n, 'montarQueryAlertas existe')
  const montar = new Function(`${js(n.getText(src).replace(/^export /, ''))}\nreturn montarQueryAlertas`)()
  assert.equal(montar({}).toString(), '')
  assert.equal(
    montar({ q: ' dg003 ', equipes: ['101', 'pessoal'], plataformas: null, leitura: 'nao_lidos', limit: 100, offset: 200 }).toString(),
    'q=dg003&equipes=101&equipes=pessoal&leitura=nao_lidos&limit=100&offset=200',
  )
  // Nenhuma caixa marcada vai como "campo=" (o servidor devolve nada).
  assert.equal(montar({ contas: [] }).toString(), 'contas=')
  assert.equal(montar({ leitura: 'todos', unread_only: true, tipos: ['tarefa_atribuida'] }).toString(), 'tipos=tarefa_atribuida&unread_only=true')
}

// ---------- 5) a marcação: barra e tabela no padrão da Tabela de Preços
const limpo = tpl.replace(/\s+/g, ' ')
assert.ok(limpo.includes('class="flex flex-wrap items-center gap-2"'), 'barra flex-wrap gap-2')
assert.ok(limpo.includes('placeholder="Buscar por SKU, conta ou aviso…"'))
assert.ok(limpo.includes('placeholder="Buscar conta…"'))
for (const filtro of ['equipe', 'plataforma', 'contas']) {
  assert.ok(limpo.includes(`class="btn btn-sm h-7 py-0" data-filtro="${filtro}"`), filtro)
}
assert.ok(limpo.includes('class="btn btn-sm h-7 py-0" data-acao="layout"'))
assert.equal((limpo.match(/>Marcar todas</g) || []).length, 3)
for (const caixa of ['Todos', 'Não lidos', 'Lidos']) assert.ok(new RegExp(`/> ${caixa} </label>`).test(limpo), caixa)
assert.ok(limpo.includes('não lidos · {{ total.toLocaleString'))
assert.ok(limpo.includes('class="overflow-auto rounded border max-h-[70vh]"'))
assert.ok(limpo.includes('<table class="w-full text-xs border-collapse">'))
assert.ok(limpo.includes('class="h-[34px] border-t hover:bg-muted/20"'))
assert.ok(limpo.includes('text-[11px] font-semibold leading-[14px]'))
const cabecalhos = [...limpo.matchAll(/<th [^>]*>([^<]*)<\/th>/g)].map((m) => m[1].trim()).filter(Boolean)
assert.deepEqual(cabecalhos, ['Quando', 'Equipe', 'Plataforma', 'Conta', 'Tipo', 'Aviso', 'Detalhe'])
// Aviso em negrito se não lido; ✓ só nos não lidos; detalhe numa linha com o texto no title.
assert.ok(limpo.includes(":class=\"!a.read_at && 'font-semibold'\""))
assert.ok(limpo.includes('v-if="!a.read_at" type="button"'))
assert.ok(limpo.includes(':title="a.message || \'\'"'))
// Saíram os selects antigos.
assert.ok(!limpo.includes('todas severidades'))
assert.ok(!limpo.includes('somente não lidos'))
// "marcar todos lidos" continua no cabeçalho.
assert.ok(limpo.includes('marcar todos lidos'))

console.log('alertas-tabela: ok')
