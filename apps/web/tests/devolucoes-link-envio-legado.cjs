// node tests/devolucoes-link-envio-legado.cjs
// Texto antigo no "Link envio" não pode congelar a linha inteira.
//
// Caso 292128 (22/09): a linha tinha o TEXTO "nao ha" gravado no link_envio, de
// antes da trava de formato (8426c57). Como `rowPatchPayload` reenvia o campo em
// todo salvamento, a guarda de formato abortava `saveRow` ANTES do PATCH — a
// operadora marcava Condição = Novo, via o select mudar e saía achando que tinha
// lançado, mas nada era salvo e o produto nunca voltava ao estoque. Qualquer
// outra edição da linha (custo, técnico, observação) morria no mesmo ponto.
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

const fonte = [pick('linkEnvioInvalido'), pick('linkEnvioLegado'), pick('rowPatchPayload')].join('\n\n')
const factory = new Function('linkEnvioTocado', transpile(`${fonte}
return { linkEnvioInvalido, linkEnvioLegado, rowPatchPayload };`))

// Linha como ela volta da listagem: só o que o payload e as guardas leem.
function linha(extra) {
  return {
    id: 'row-292128',
    sku: 'dg056.ci',
    pedido_bling: '292128',
    motivo_devolucao: 'Bloqueado',
    condicao_produto: 'Novo',
    link_abertura: 'https://drive.google.com/x',
    link_envio: null,
    custo_produto: null,
    custo_manutencao: 80,
    reembolso: true,
    tecnico: null,
    devolver_estoque: true,
    observacao: null,
    quantidade: 1,
    troca_sku: null,
    troca_condicao: null,
    estoque_suffix: null,
    manutencao_destino: null,
    estoque_destino_sku: null,
    estoque_nova_tag: null,
    ...extra,
  }
}

// 1) Texto gravado que o operador NÃO tocou: é legado. Fica de fora do payload
//    (o schema recusaria com 422) e não barra o salvamento da linha.
{
  const { linkEnvioLegado, rowPatchPayload } = factory({ value: new Set() })
  const row = linha({ link_envio: 'nao ha' })
  assert.equal(linkEnvioLegado(row), true, 'texto antigo não tocado = legado')
  const payload = rowPatchPayload(row)
  assert.equal('link_envio' in payload, false, 'não reenvia o texto legado')
  assert.equal(payload.condicao_produto, 'Novo', 'o resto da linha vai normalmente')
  assert.equal(payload.devolver_estoque, true, 'o lançamento de estoque sai junto')
}

// 2) Texto digitado AGORA: a trava continua valendo (era isso que ela veio
//    resolver — "nao ha, nao recebido" ia parar no QR do cartão da disputa).
{
  const { linkEnvioInvalido, linkEnvioLegado } = factory({ value: new Set(['row-292128']) })
  const row = linha({ link_envio: 'nao ha' })
  assert.equal(linkEnvioLegado(row), false, 'campo tocado não é legado')
  assert.equal(linkEnvioInvalido(row.link_envio) && !linkEnvioLegado(row), true, 'a guarda barra o save')
}

// 3) Apagar o texto: vai como null e limpa o campo de vez.
{
  const { linkEnvioLegado, rowPatchPayload } = factory({ value: new Set(['row-292128']) })
  const row = linha({ link_envio: null })
  assert.equal(linkEnvioLegado(row), false)
  const payload = rowPatchPayload(row)
  assert.equal('link_envio' in payload, true)
  assert.equal(payload.link_envio, null, 'campo limpo é enviado como null')
}

// 4) Link de verdade: passa e é enviado.
{
  const { linkEnvioInvalido, linkEnvioLegado, rowPatchPayload } = factory({ value: new Set(['row-292128']) })
  const row = linha({ link_envio: 'https://drive.google.com/file/abc' })
  assert.equal(linkEnvioInvalido(row.link_envio), false)
  assert.equal(linkEnvioLegado(row), false)
  assert.equal(rowPatchPayload(row).link_envio, 'https://drive.google.com/file/abc')
}

// 5) A guarda de `saveRow` é a que isenta o legado — se alguém voltar a chamar
//    `linkEnvioInvalido` sozinho ali, a linha congela de novo.
{
  const saveRow = ast.statements.find(
    (n) => ts.isFunctionDeclaration(n) && n.name?.text === 'saveRow',
  )
  assert.ok(saveRow, 'a página ainda tem saveRow')
  const corpo = src.slice(saveRow.getStart(ast), saveRow.getEnd())
  assert.match(corpo, /linkEnvioInvalido\(row\.link_envio\) && !linkEnvioLegado\(row\)/)
  assert.match(corpo, /bloqueiaSave\(/, 'guarda barrada avisa com toast, não só com o banner do topo')
}

console.log('ok devolucoes-link-envio-legado (5 casos)')
