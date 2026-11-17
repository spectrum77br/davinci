// Run from apps/web: node tests/tarefas-concluir.cjs
// Tarefas (17/11/2026): botão no canto de cada linha (e do cartão no celular) para
// marcar a tarefa como feita ("Concluir" grava a conclusão = hoje) ou voltar para
// pendente ("Concluída", com confirmação). Antes só dava abrindo a tarefa e digitando
// a data. Só admin — a API só deixa admin mudar a data de conclusão.
// Compila o template de verdade e roda o script da página com uma API falsa.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/tarefas.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'tarefas' })
const compiled = compileTemplate({
  source: descriptor.template.content, filename, id: 'tarefas',
  compilerOptions: { bindingMetadata: script.bindings },
})
assert.deepEqual(compiled.errors, [])

// ── Template: o botão fica no canto, só para admin, e não abre a tarefa ao clicar.
const tpl = descriptor.template.content
const botoes = [...tpl.matchAll(/<button\s[^>]*?data-concluir[\s\S]*?<\/button>/g)].map((m) => m[0])
assert.equal(botoes.length, 2, 'um na tabela, um no cartão do celular')
for (const b of botoes) {
  assert.match(b, /@click\.stop="alternarFeita\(t\)"/, 'clicar no botão não abre a edição da linha')
  assert.match(b, /:disabled="marcando === t\.id"/)
  assert.match(b, /\{\{ t\.data_conclusao \? 'Concluída' : 'Concluir' \}\}/)
  assert.match(b, /Clique para voltar a pendente/)
}
assert.match(tpl, /<td v-if="isAdmin" class="px-3 py-2 text-right whitespace-nowrap">\s*<button/, 'última coluna da tabela, só admin')
assert.match(tpl, /<th v-if="isAdmin" class="px-3 py-2 text-right">Feita\?<\/th>/)
assert.match(tpl, /<td :colspan="isAdmin \? 6 : 5"/)
assert.match(tpl, /<button\s+v-if="isAdmin"\s+type="button"\s+data-concluir/, 'no celular: o botão no canto do cartão, só admin')
assert.match(tpl, /<span\s+v-else-if="!t\.data_conclusao"[^>]*>pendente<\/span>/, 'quem não é admin continua vendo "pendente"')

// ── Script: roda o setup com a API falsa.
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const setupSrc = descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const js = ts.transpileModule(setupSrc, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText

const EU = 'u-eu'
const linha = (id, resp, conclusao) => ({
  id, responsavel_id: resp, responsavel_name: resp, responsavel_email: null, data_inicio: '2026-11-15',
  data_conclusao: conclusao, tarefa: `tarefa ${id}`, observacao: null, created_by: null,
  created_at: '2026-11-15T12:00:00Z', updated_at: '2026-11-15T12:00:00Z',
})

async function montar({ admin = true, falhar = false, confirma = true, pendentes = 3 } = {}) {
  const chamadas = []
  const avisos = []
  const estado = { 'davinci.pendentes.tarefas': Vue.ref(pendentes) }
  const api = async (url, opts = {}) => {
    chamadas.push({ url, method: opts.method ?? 'GET', body: opts.body })
    if (url === '/api/tarefas') return [linha('A', EU, null), linha('B', 'u-outro', '2026-11-16')]
    if (url.startsWith('/api/users')) return { items: [] }
    if (falhar) throw { data: { detail: { code: 'falhou_teste' } } }
    const id = url.split('/').at(-1)
    return { ...linha(id, id === 'A' ? EU : 'u-outro', opts.body.data_conclusao), updated_at: '2026-11-17T12:00:00Z' }
  }
  const globais = {
    computed: Vue.computed, ref: Vue.ref,
    useApi: () => ({ api }),
    useAuthStore: () => ({ isAdmin: admin, user: { id: EU, role: admin ? 'admin' : 'user' } }),
    useState: (k, ini) => estado[k] ?? Vue.ref(ini()),
    useToasts: () => ({ error: (titulo, texto) => avisos.push([titulo, texto]) }),
    isoToday: () => '2026-11-17',
    confirm: () => confirma,
    navigator: {}, setTimeout,
    TABS_CONFIGURACOES: [], TABS_USUARIOS: [],
    Plus: {}, RefreshCw: {}, X: {}, Trash2: {}, Bot: {}, Copy: {}, Check: {}, Loader2: {},
  }
  const nomes = Object.keys(globais)
  const s = await new AsyncFunction(...nomes, `${js}\nreturn { rows, alternarFeita, marcando, minhasPendentes }`)(
    ...nomes.map((n) => globais[n]))
  return { s, chamadas, avisos, estado, cfg: (v) => { globais.confirm = () => v } }
}

const patches = (c) => c.filter((x) => x.method === 'PATCH')

;(async () => {
  // Admin conclui a própria tarefa: grava hoje, a linha muda no lugar e a bolinha cai na hora.
  {
    const { s, chamadas } = await montar()
    const a = s.rows.value[0]
    await s.alternarFeita(a)
    assert.deepEqual(patches(chamadas), [{ url: '/api/tarefas/A', method: 'PATCH', body: { data_conclusao: '2026-11-17' } }])
    assert.equal(s.rows.value[0].id, 'A', 'continua no mesmo lugar')
    assert.equal(s.rows.value[0].data_conclusao, '2026-11-17')
    assert.equal(s.rows.value[0].tarefa, 'tarefa A')
    assert.equal(s.minhasPendentes.value, 2)
    assert.equal(s.marcando.value, null)
  }
  // Voltar para pendente pede confirmação; sem confirmar, nada muda.
  {
    const { s, chamadas } = await montar({ confirma: false })
    await s.alternarFeita(s.rows.value[1])
    assert.deepEqual(patches(chamadas), [])
    assert.equal(s.rows.value[1].data_conclusao, '2026-11-16')
  }
  {
    const { s, chamadas } = await montar({ confirma: true })
    await s.alternarFeita(s.rows.value[1])
    assert.deepEqual(patches(chamadas), [{ url: '/api/tarefas/B', method: 'PATCH', body: { data_conclusao: null } }])
    assert.equal(s.rows.value[1].data_conclusao, null)
    assert.equal(s.minhasPendentes.value, 3, 'tarefa de outra pessoa não mexe na minha bolinha')
  }
  // Erro da API: avisa, não muda a linha e libera o botão.
  {
    const { s, avisos } = await montar({ falhar: true })
    await s.alternarFeita(s.rows.value[0])
    assert.deepEqual(avisos, [['Não deu para marcar a tarefa', 'falhou_teste']])
    assert.equal(s.rows.value[0].data_conclusao, null)
    assert.equal(s.minhasPendentes.value, 3)
    assert.equal(s.marcando.value, null)
  }
  // Quem não é admin: nada (a API ignoraria a data de conclusão).
  {
    const { s, chamadas } = await montar({ admin: false })
    await s.alternarFeita(s.rows.value[0])
    assert.deepEqual(patches(chamadas), [])
  }
  // Duplo clique: o segundo é ignorado enquanto o primeiro salva.
  {
    const { s, chamadas } = await montar()
    await Promise.all([s.alternarFeita(s.rows.value[0]), s.alternarFeita(s.rows.value[0])])
    assert.equal(patches(chamadas).length, 1)
  }
  console.log('tarefas-concluir: ok')
})().catch((e) => { console.error(e); process.exit(1) })
