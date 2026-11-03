// Run from apps/web: node tests/lojas-equipe.cjs
// Equipe da loja (03/11/2026): só o número (Equipe 1/2/3) — rótulo real da
// página de Lojas, as três opções no select e o aviso antes de trocar
// (trocar a equipe troca quem vê a loja).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/store-info.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const equipes = source.statements.find((s) => ts.isVariableStatement(s)
  && s.declarationList.declarations.some((d) => d.name.getText(source) === 'EQUIPES_DA_LOJA'))
const rotulo = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === 'rotuloEquipeLoja')
assert.ok(equipes && rotulo, 'EQUIPES_DA_LOJA and rotuloEquipeLoja exist')
const compiled = ts.transpileModule(`${equipes.getText(source)}\n${rotulo.getText(source)}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const { EQUIPES_DA_LOJA, rotuloEquipeLoja } = new Function(
  `${compiled.outputText}\nreturn { EQUIPES_DA_LOJA, rotuloEquipeLoja }`)()

assert.deepEqual([...EQUIPES_DA_LOJA], [1, 2, 3])
assert.equal(rotuloEquipeLoja(1), 'Equipe 1')
assert.equal(rotuloEquipeLoja(2), 'Equipe 2')
assert.equal(rotuloEquipeLoja(3), 'Equipe 3')
assert.equal(rotuloEquipeLoja(null), 'Sem equipe')
assert.equal(rotuloEquipeLoja(undefined), 'Sem equipe')

const tpl = descriptor.template.content
// As três opções saem de EQUIPES_DA_LOJA; nada de "Equipe 1/2" escrito à mão.
assert.match(tpl, /<option value="">Sem equipe<\/option>\s*<option v-for="n in EQUIPES_DA_LOJA"[^>]*>Equipe \{\{ n \}\}<\/option>/)
// Sem nome de equipe na tela (pedido do dono: só o número).
assert.doesNotMatch(tpl + script, /Londres|Israel|Marrocos/)
assert.doesNotMatch(tpl, /`Equipe \$\{row\.commercial_team\}`/)
// O cartão do celular e a célula da tabela usam o mesmo rótulo.
assert.equal((tpl.match(/rotuloEquipeLoja\(row\.commercial_team\)/g) || []).length, 2)
// Trocar a equipe pede confirmação e fecha a edição antes (o blur não grava duas vezes).
const inicio = script.indexOf("} else if (field === 'commercial_team') {")
const ramo = script.slice(inicio, script.indexOf("} else if (field === 'password') {", inicio))
assert.ok(inicio > 0 && ramo.length > 0, 'commercial_team branch found')
assert.match(ramo, /cancelEdit\(\)\s*\n\s*if \(!confirm\(aviso\)\) return/)
assert.match(ramo, /passa a aparecer para a \$\{depois\}/)
console.log('lojas-equipe: ok')

// Usuários › Acesso às contas: os vínculos 101/102/103 aparecem como Equipe 1/2/3.
const usuarios = parse(fs.readFileSync(path.resolve(__dirname, '../pages/users/[id].vue'), 'utf8'),
  { filename: 'users/[id].vue' }).descriptor
assert.match(usuarios.scriptSetup.content, /const EQUIPE_DO_VINCULO: Record<number, number> = \{ 101: 1, 102: 2, 103: 3 \}/)
assert.match(usuarios.template.content, /<strong v-if="option\.equipe"[^>]*>Equipe \{\{ option\.equipe \}\} —<\/strong> \{\{ option\.label \}\}/)
assert.match(usuarios.template.content, /<option :value="3">Equipe 3<\/option>/)
assert.doesNotMatch(usuarios.template.content, /Londres|Israel|Marrocos/)
console.log('usuarios-equipe: ok')
