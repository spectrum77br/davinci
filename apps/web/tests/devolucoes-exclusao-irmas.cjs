// node tests/devolucoes-exclusao-irmas.cjs
// Excluir um lançamento (caso 300978, revisão de 26/10): o que o back desfez
// tem que chegar às linhas IRMÃS que ficam na tabela.
//
// * Situação que VOLTOU (situacao_revertida) — não só a recalculada: senão a
//   irmã continua mostrando "Problemas" no select (escolher Problemas de novo
//   não dispara @change) e o confirm seguinte diz "(hoje: Problemas)".
// * Chamado apagado: a irmã continua com o selo "chamado em aberto" e o
//   confirm seguinte promete apagar um chamado que já não existe. Relê a
//   irmã pelo GET (sem load(), que perderia edições não salvas).
//
// Roda as funções REAIS da página, extraídas do <script setup>.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const pageFile = path.join(__dirname, '../pages/devolucoes.vue')
const page = parse(fs.readFileSync(pageFile, 'utf8'), { filename: pageFile })
assert.deepEqual(page.errors, [])
const src = page.descriptor.scriptSetup.content
const ast = ts.createSourceFile('devolucoes.ts', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

function pick(name) {
  const node = ast.statements.find((n) => ts.isFunctionDeclaration(n) && n.name?.text === name)
  assert.ok(node, `a página ainda tem a função ${name}`)
  return src.slice(node.getStart(ast), node.getEnd())
}
function pickVar(name) {
  const node = ast.statements.find(
    (n) => ts.isVariableStatement(n) && n.declarationList.declarations.some((d) => d.name.text === name),
  )
  return node ? src.slice(node.getStart(ast), node.getEnd()) : ''
}

const fonte = [
  pickVar('CAMPOS_CHAMADO'),
  pick('avisarExclusao'),
  ast.statements.some((n) => ts.isFunctionDeclaration(n) && n.name?.text === 'atualizarChamadoDasIrmas')
    ? pick('atualizarChamadoDasIrmas')
    : '',
  'return { avisarExclusao }',
].join('\n')

function montar(linhas, respostasGet = {}) {
  const items = { value: linhas }
  const toasts = []
  const gets = []
  let acompanhamento = 0
  const fn = new Function('items', 'pushToast', 'brl', 'api', 'loadAcompanhamento', transpile(fonte))
  const { avisarExclusao } = fn(
    items,
    (t) => { toasts.push(t); return toasts.length },
    (v) => `R$ ${Number(v || 0).toFixed(2)}`,
    async (url) => {
      gets.push(url)
      const id = decodeURIComponent(url.split('/').pop())
      if (!(id in respostasGet)) throw new Error(`GET inesperado: ${url}`)
      return respostasGet[id]
    },
    () => { acompanhamento += 1 },
  )
  return { items, toasts, gets, avisarExclusao, acompanhamentos: () => acompanhamento }
}

const tick = () => new Promise((r) => setTimeout(r, 0))

async function main() {
  // 1) Situação que voltou chega à irmã (b045.16 sem condição fica).
  {
    const excluida = { id: 'a', pedido_bling: '300978', situacao_bling_id: 83960, situacao_bling_nome: 'Problemas' }
    const irma = { id: 'b', pedido_bling: '300978', situacao_bling_id: 83960, situacao_bling_nome: 'Problemas' }
    const outra = { id: 'c', pedido_bling: '111', situacao_bling_id: 83960, situacao_bling_nome: 'Problemas' }
    const t = montar([irma, outra])
    t.avisarExclusao(excluida, {
      ok: true, estoque_estornado: false, mensagem: null,
      situacao_revertida: { de: '83960', para: 83957, nome: 'Aguardando Devolução' },
      chamado_apagado: false,
    })
    assert.equal(irma.situacao_bling_id, 83957, 'a irmã mostra a situação que voltou')
    assert.equal(irma.situacao_bling_nome, 'Aguardando Devolução')
    assert.equal(outra.situacao_bling_id, 83960, 'outro pedido não muda')
    assert.equal(t.acompanhamentos(), 1)
  }

  // 2) Chamado apagado: a irmã é relida pelo GET e perde o selo.
  {
    const excluida = { id: 'a', pedido_bling: '300978' }
    const irma = {
      id: 'b', pedido_bling: '300978', tem_chamado: true, chamado_numero: null,
      chamado_resolvido: false, chamado_plataforma: 'shopee', chamado_ml_status: 'pendente',
      chamado_ml_erro: null, chamado_enviada_at: null, observacao: 'digitando…',
    }
    const t = montar([irma], {
      b: {
        id: 'b', pedido_bling: '300978', tem_chamado: false, chamado_numero: null,
        chamado_resolvido: null, chamado_plataforma: null, chamado_ml_status: null,
        chamado_ml_erro: null, chamado_enviada_at: null, observacao: 'do banco',
      },
    })
    t.avisarExclusao(excluida, {
      ok: true, estoque_estornado: false, mensagem: null, chamado_apagado: true,
    })
    await tick(); await tick()
    assert.deepEqual(t.gets, ['/api/devolutions/b'])
    assert.equal(irma.tem_chamado, false, 'selo do chamado some')
    assert.equal(irma.chamado_ml_status, null)
    assert.equal(irma.chamado_plataforma, null)
    assert.equal(irma.observacao, 'digitando…', 'só os campos do chamado — a edição não salva fica')
  }

  // 3) Sem chamado mexido e sem situação: nada relido.
  {
    const t = montar([{ id: 'b', pedido_bling: '300978' }])
    t.avisarExclusao({ id: 'a', pedido_bling: '300978' }, {
      ok: true, estoque_estornado: false, mensagem: null, chamado_apagado: false,
    })
    await tick()
    assert.deepEqual(t.gets, [])
    assert.equal(t.acompanhamentos(), 0)
  }
  console.log('OK: situação revertida e chamado apagado chegam às linhas irmãs.')
}

main().catch((err) => { console.error(err); process.exitCode = 1 })
