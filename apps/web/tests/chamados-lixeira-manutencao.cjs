// node tests/chamados-lixeira-manutencao.cjs
// Lixeira do chamado (caso 300978, revisão de 26/10): a linha que passou por
// Manutenção e saiu (Usado, manutencao=true) leva junto o débito do conserto —
// a prévia tem que avisar, como a lixeira da aba Devoluções já avisa.
//
// Compila o template REAL do ChamadosPainel e renderiza só o trecho da lista
// de lançamentos com dados falsos.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse, compileTemplate } = require('vue/compiler-sfc')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')

const file = path.join(__dirname, '../components/ChamadosPainel.vue')
const sfc = parse(fs.readFileSync(file, 'utf8'), { filename: file })
assert.deepEqual(sfc.errors, [])
const tpl = sfc.descriptor.template.content

// O trecho da lista de lançamentos da janela "Excluir chamado".
const ini = tpl.indexOf('v-for="l in excluir.preview.lancamentos"')
assert.ok(ini > 0, 'a janela ainda lista os lançamentos')
const abre = tpl.lastIndexOf('<label', ini)
const fecha = tpl.indexOf('</label>', ini) + '</label>'.length
const trecho = `<div>${tpl.slice(abre, fecha)}</div>`
const compiled = compileTemplate({ source: trecho, filename: file, id: 'lixeira-manut' })
assert.deepEqual(compiled.errors, [])
const mod = {}
new Function('exports', 'require', ts.transpileModule(compiled.code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText)(mod, require)

async function render(lancamentos) {
  const app = Vue.createSSRApp({
    setup: () => ({
      excluir: { preview: { lancamentos }, selecionados: new Set(), saving: false },
      excluirPodeLancamentos: true,
      toggleLancamento: () => {},
      lancamentoTexto: (l) => l.sku,
      estoqueTexto: () => ({ texto: '', alerta: false }),
      CONDICOES_COM_PREJUIZO: ['Extraviado', 'Sucata', 'Manutenção'],
    }),
    render: mod.render,
  })
  return renderToString(app)
}

async function main() {
  const base = { id: '1', sku: 'b045.12', estoque_estornavel: false, marcado_padrao: false }
  const usado = await render([{ ...base, condicao_produto: 'Usado', manutencao: true }])
  assert.match(usado, /débito do conserto \(Manutenção\)/, 'avisa que o débito do conserto vai junto')

  const semManut = await render([{ ...base, condicao_produto: 'Usado', manutencao: false }])
  assert.doesNotMatch(semManut, /débito do conserto/)

  // Ainda em Manutenção: o aviso do prejuízo da condição já cobre (sem duplicar).
  const emManut = await render([{ ...base, condicao_produto: 'Manutenção', manutencao: true }])
  assert.doesNotMatch(emManut, /débito do conserto \(Manutenção\)/)
  console.log('OK: a lixeira do chamado avisa do débito do conserto da linha que saiu de Manutenção.')
}

main().catch((err) => { console.error(err); process.exitCode = 1 })
