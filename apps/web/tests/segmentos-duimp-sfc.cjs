// node tests/segmentos-duimp-sfc.cjs — Admin › Segmentos › coluna "DUIMP / mensagem"
// (migration 0487, Eduardo 16/11/2026): o texto que vai na nota/etiqueta quando o
// produto não tem DUIMP na Importação. Trava:
//  - a coluna existe no cabeçalho e em toda linha (linha normal, "adicionar raiz",
//    "adicionar subsegmento"), e os colspan acompanham (10 colunas);
//  - raiz e subsegmento editam (clique na célula, texto de uma linha, até 200);
//  - subsegmento vazio mostra em cinza o texto herdado do pai ("↳ …");
//  - "|" é barrado na tela (o " | " separa as linhas da etiqueta).
// Só dados FALSOS; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')

const transpile = (src) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText

function sfc(rel) {
  const filename = path.join(__dirname, '..', rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const t = compileTemplate({ source: descriptor.template.content, filename, id: 'seg-duimp', compilerOptions: { expressionPlugins: ['typescript'] } })
  assert.deepEqual(t.errors, [], `${rel}: template`)
  return descriptor
}

// ---------------------------------------------------------------- página
const pagina = sfc('pages/admin/segments.vue')
{
  const tpl = pagina.template.content
  const script = pagina.scriptSetup.content
  const ths = tpl.match(/<th\b/g).length
  assert.equal(ths, 10, '10 colunas no cabeçalho')
  assert.ok(tpl.includes('DUIMP / mensagem'), 'cabeçalho da coluna')
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], String(ths), 'colspan acompanha o cabeçalho')
  // linha "adicionar raiz": uma <td> por coluna
  const addRaiz = tpl.slice(tpl.indexOf('<!-- add new root -->'))
  const tr = addRaiz.slice(0, addRaiz.indexOf('</tr>'))
  const tds = (tr.match(/<td\b(?![^>]*v-for)/g) || []).length + 4 // + as 4 medidas do v-for
  assert.equal(tds, ths, 'linha "adicionar raiz" com todas as colunas')
  assert.match(script, /duimp_padrao: string \| null/)
  assert.match(script, /type EditField = 'name' \| 'min_margin' \| DimField \| 'duimp_padrao'/)
  assert.match(script, /field === 'duimp_padrao' && raw\.includes\('\|'\)/, 'barra o "|" na tela')
}

// ---------------------------------------------------------------- linha (render de verdade)
const linha = sfc('components/SegmentRow.vue')
const Row = (() => {
  const { content } = compileScript(linha, { id: 'seg-duimp', inlineTemplate: true })
  const mod = { exports: {} }
  const req = (m) => (m === 'lucide-vue-next' ? new Proxy({}, { get: () => ({ render: () => null }) }) : require(m))
  // `computed` vem do auto-import do Nuxt.
  new Function('module', 'exports', 'require', `const { computed } = require('vue');\n${transpile(content)}`)(mod, mod.exports, req)
  const comp = mod.exports.default
  comp.name = 'SegmentRow'
  comp.components = { SegmentRow: comp }
  return comp
})()

const seg = (id, nome, duimp, filhos = []) => ({
  id, user_id: null, parent_id: null, name: nome, slug: id, sort_order: 0, active: true,
  min_margin: null, altura: null, largura: null, comprimento: null, peso: null,
  duimp_padrao: duimp, special_dates: [], created_at: '', updated_at: '', children: filhos,
})

async function html(node, extra = {}) {
  const app = Vue.createSSRApp({
    render: () => Vue.h(Row, {
      node, depth: 0, expanded: new Set([node.id]), editing: null, editValue: '', flashed: new Set(),
      canEdit: true, canDelete: false, addingUnder: undefined, newName: '', adding: false,
      setEditInputRef: () => {}, ...extra,
    }),
  })
  return renderToString(app)
}

;(async () => {
  const MSG_MALA = 'produto importado pela duimp 26BR0009999999-1'
  const MSG_24 = 'mala 24 importada pela duimp 26BR0002222222-2'
  const arvore = seg('mala', 'Mala', MSG_MALA, [
    seg('m24', '24" acima', MSG_24),
    seg('acess', 'Acessórios', null),
  ])
  const h = await html(arvore)
  // cada linha: 10 células (nome, margem, condição, 4 medidas, DUIMP, ativo, ações)
  for (const tr of h.match(/<tr[\s\S]*?<\/tr>/g)) assert.equal((tr.match(/<td\b/g) || []).length, 10, 'linha com 10 células')
  assert.ok(h.includes(MSG_MALA), 'texto da raiz')
  assert.ok(h.includes(MSG_24), 'texto do subsegmento')
  assert.ok(h.includes(`↳ ${MSG_MALA}`), 'subsegmento vazio mostra o herdado')
  assert.equal(h.split(`↳ ${MSG_24}`).length, 1, 'quem tem texto próprio não mostra herança')

  // sem texto em lugar nenhum: traço
  const vazio = await html(seg('ele', 'Eletro', null, [seg('e1', '1', null)]))
  assert.ok(!vazio.includes('↳'), 'nada herdado')

  // editando: input de texto até 200 caracteres
  const ed = await html(seg('cel', 'Celular', 'x'), { editing: { id: 'cel', field: 'duimp_padrao' }, editValue: 'x' })
  assert.match(ed, /<input[^>]*type="text"[^>]*maxlength="200"/, 'campo de texto com limite')

  // linha "adicionar subsegmento" com as 10 células
  const add = await html(seg('cel', 'Celular', null), { addingUnder: 'cel' })
  const trs = add.match(/<tr[\s\S]*?<\/tr>/g)
  assert.equal((trs[1].match(/<td\b/g) || []).length, 10, 'linha "adicionar subsegmento" com 10 células')

  console.log('segmentos-duimp-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
