// node tests/link-mega.cjs
// Vídeo da embalagem/expedição só na MEGA (Vinicius, 23/09): a tela avisa na
// hora com a mesma regra do backend (apps/api/app/services/link_mega.py).
// O que vem depois do # é a chave que abre o vídeo — sem ela ninguém assiste.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const src = fs.readFileSync(path.join(__dirname, '../lib/linkMega.ts'), 'utf8')
const js = ts.transpileModule(src, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const { erroLinkMega, MSG_LINK_NAO_MEGA, MSG_LINK_MEGA_SEM_CHAVE, MSG_LINK_MEGA_PASTA } = mod.exports

const CHAVE = 'vK2VCD7kON8nv9zplMPFaPfWSzP5jxYrhprVtjQd3V8'
const casos = [
  [`https://mega.nz/file/oMV0HRyS#${CHAVE}`, null],
  [`mega.nz/file/oMV0HRyS#${CHAVE}`, null], // sem https: o backend completa
  [`  https://mega.nz/file/oMV0HRyS#${CHAVE}  `, null],
  [`https://mega.nz/#!oMV0HRyS!${CHAVE}`, null], // formato antigo
  [`https://mega.nz/folder/AbCdEfGh#Kk0123456789abcdefghij/file/XyZ12345`, null],
  ['https://mega.nz/file/oMV0HRyS', MSG_LINK_MEGA_SEM_CHAVE],
  ['https://mega.nz/file/oMV0HRyS#', MSG_LINK_MEGA_SEM_CHAVE],
  ['https://mega.nz/folder/AbCdEfGh#Kk0123456789abcdefghij', MSG_LINK_MEGA_PASTA],
  ['https://drive.google.com/file/d/1AbC/view?usp=sharing', MSG_LINK_NAO_MEGA],
  [`https://mega.nz/file/oMV0HRyS #${CHAVE}`, MSG_LINK_NAO_MEGA],
  ['https://mega.io', MSG_LINK_NAO_MEGA],
  ['ok', MSG_LINK_NAO_MEGA],
  ['', MSG_LINK_NAO_MEGA],
  [null, MSG_LINK_NAO_MEGA],
]
for (const [link, esperado] of casos) assert.equal(erroLinkMega(link), esperado, String(link))

console.log(`ok link-mega (${casos.length} casos)`)
