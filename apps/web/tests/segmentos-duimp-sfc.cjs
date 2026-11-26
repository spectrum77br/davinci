// node tests/segmentos-duimp-sfc.cjs — Admin › Segmentos › coluna "DUIMP / mensagem"
// (migration 0488, Eduardo 16/11/2026): o texto que vai na nota/etiqueta quando o
// produto não tem DUIMP na Importação. Trava:
//  - a coluna existe no cabeçalho e em toda linha (linha normal, "adicionar raiz",
//    "adicionar subsegmento"), e os colspan acompanham (10 colunas);
//  - raiz e subsegmento editam (clique na célula, texto de uma linha, até 75 — o que cabe na etiqueta);
//  - subsegmento vazio mostra em cinza o texto herdado do pai ("↳ …");
//  - "|" é barrado na tela sem perder o texto digitado (o " | " separa as linhas
//    da etiqueta); o 422 do servidor vira frase de gente;
//  - quem só vê não ganha cursor/dica de edição.
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

// ---------------------------------------------------------------- Margem (M-74)
// O selo "especial" acende também pela Condição especial do PRODUTO (Produtos 2, vale primeiro):
// a dica não pode mandar procurar só em Cadastros → Segmentos.
{
  const margem = sfc('components/MargemPainel.vue').template.content
  const selo = margem.slice(margem.lastIndexOf('<span', margem.indexOf('>especial</span>')), margem.indexOf('>especial</span>'))
  assert.match(selo, /v-if="r\.data_especial"/)
  assert.doesNotMatch(selo, /Condição Especial do segmento ativa/)
  assert.match(selo, /A do produto \(cadastro do produto no Produtos 2\) vale primeiro; sem ela, a do segmento \(Cadastros → Segmentos\)/)
}

// ---------------------------------------------------------------- página
const pagina = sfc('pages/admin/segments.vue')
{
  const tpl = pagina.template.content
  const script = pagina.scriptSetup.content
  const ths = tpl.match(/<th\b/g).length
  assert.equal(ths, 10, '10 colunas no cabeçalho')
  assert.ok(tpl.includes('DUIMP / mensagem'), 'cabeçalho da coluna')
  // M-74: a janela de Condição Especial avisa que a do produto (Produtos 2) vale primeiro.
  assert.ok(
    tpl.replace(/\s+/g, ' ').includes('Se o produto tiver Condição especial própria (Produtos 2) no dia do pedido, vale só a dele.'),
    'ajuda da Condição Especial cita a do produto',
  )
  // M-74: a dica do cabeçalho "DUIMP / mensagem" diz que a DUIMP padrão do produto vem antes.
  {
    const th = tpl.slice(tpl.lastIndexOf('<th', tpl.indexOf('DUIMP / mensagem')), tpl.indexOf('DUIMP / mensagem'))
    assert.match(th, /title="Vai na nota e na etiqueta \(nota cheia\) quando o produto não tem DUIMP na Importação nem DUIMP padrão própria \(cadastro do produto no Produtos 2\)\./, 'dica do cabeçalho com a ordem nova')
  }
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], String(ths), 'colspan acompanha o cabeçalho')
  // linha "adicionar raiz": uma <td> por coluna
  const addRaiz = tpl.slice(tpl.indexOf('<!-- add new root -->'))
  const tr = addRaiz.slice(0, addRaiz.indexOf('</tr>'))
  const tds = (tr.match(/<td\b(?![^>]*v-for)/g) || []).length + 4 // + as 4 medidas do v-for
  assert.equal(tds, ths, 'linha "adicionar raiz" com todas as colunas')
  assert.match(script, /duimp_padrao: string \| null/)
  assert.match(script, /type EditField = 'name' \| 'min_margin' \| DimField \| 'duimp_padrao'/)
  assert.match(script, /field === 'duimp_padrao' && raw\.includes\('\|'\)/, 'barra o "|" na tela')
  {
    const i = script.indexOf("field === 'duimp_padrao' && raw.includes('|')")
    const bloco = script.slice(i, script.indexOf('}', i))
    assert.ok(!bloco.includes('cancelEdit'), 'com "|" continua editando (não joga o texto fora)')
  }
  for (const codigo of ['duimp_padrao_sem_barra', 'duimp_padrao_muito_longo', 'duimp_padrao_caractere_invalido']) {
    assert.ok(script.includes(codigo), `frase para o 422 ${codigo}`)
  }
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

  // editando: input de texto até 75 caracteres
  const ed = await html(seg('cel', 'Celular', 'x'), { editing: { id: 'cel', field: 'duimp_padrao' }, editValue: 'x' })
  assert.match(ed, /<input[^>]*type="text"[^>]*maxlength="75"/, 'campo de texto com limite')

  // quem só vê: sem cursor de clique nem dica de edição na célula
  const so = await html(seg('cel', 'Celular', null), { canEdit: false })
  assert.ok(!so.includes('Clique para escrever'), 'sem dica de edição para quem só vê')
  const ve = await html(seg('cel', 'Celular', null))
  assert.ok(ve.includes('Clique para escrever'), 'dica de edição para quem edita')

  // linha "adicionar subsegmento" com as 10 células
  const add = await html(seg('cel', 'Celular', null), { addingUnder: 'cel' })
  const trs = add.match(/<tr[\s\S]*?<\/tr>/g)
  assert.equal((trs[1].match(/<td\b/g) || []).length, 10, 'linha "adicionar subsegmento" com 10 células')

  console.log('segmentos-duimp-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
