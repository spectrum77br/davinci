// Conferência da TELA para o robô de mudanças (roda DENTRO da caixa, depois do
// Aprovar). Compila os arquivos mexidos como o build faria — sintaxe do SFC,
// do template e do TypeScript — e confere que os imports locais (~/, @/, ./,
// ../) existem. Sem isso, um erro de template ia para o main, o deploy falhava
// e o main ficava quebrado para todo mundo (revisão de 17/11/2026).
//
//   node conferir_web.cjs apps/web/pages/x.vue apps/web/lib/y.ts ...
// (caminhos relativos à cópia do pedido; arquivo apagado é ignorado)
'use strict'
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const sfc = require('vue/compiler-sfc')

if (typeof sfc.registerTS === 'function') sfc.registerTS(() => ts)

const WEB = path.resolve('apps/web')
const erros = []
let conferidos = 0

function sintaxe(codigo, nome) {
  const r = ts.transpileModule(codigo, {
    fileName: nome,
    reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve },
  })
  for (const d of r.diagnostics || []) {
    erros.push(`${nome}: ${ts.flattenDiagnosticMessageText(d.messageText, '\n')}`)
  }
}

const EXT = ['', '.ts', '.js', '.mjs', '.vue', '.json', '.d.ts', '/index.ts', '/index.js', '/index.vue']
function imports(codigo, arquivo, rel) {
  const re = /(?:import|export)\s[^'"`;]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|import\s+['"]([^'"]+)['"]/g
  let m
  while ((m = re.exec(codigo))) {
    const spec = (m[1] || m[2] || m[3] || '').replace(/[?#].*$/, '')
    let base = null
    if (spec.startsWith('~~/') || spec.startsWith('@@/')) base = path.join(WEB, spec.slice(3))
    else if (spec.startsWith('~/') || spec.startsWith('@/')) base = path.join(WEB, spec.slice(2))
    else if (spec.startsWith('./') || spec.startsWith('../')) base = path.resolve(path.dirname(arquivo), spec)
    if (!base) continue // pacote (vue, lucide…) ou alias do Nuxt (#imports)
    if (!EXT.some((e) => fs.existsSync(base + e))) erros.push(`${rel}: import não encontrado: '${spec}'`)
  }
}

for (const rel of process.argv.slice(2)) {
  const arquivo = path.resolve(rel)
  if (!fs.existsSync(arquivo)) continue
  const codigo = fs.readFileSync(arquivo, 'utf8')
  conferidos++
  if (rel.endsWith('.vue')) {
    const { descriptor, errors } = sfc.parse(codigo, { filename: arquivo })
    for (const e of errors) erros.push(`${rel}: ${e.message || e}`)
    if (errors.length) continue
    const bloco = descriptor.scriptSetup || descriptor.script
    const lang = (bloco && bloco.lang) || 'js'
    let bindings
    if (bloco) {
      try {
        const s = sfc.compileScript(descriptor, { id: 'conferir', inlineTemplate: false })
        bindings = s.bindings
        sintaxe(s.content, `${rel}.${lang === 'ts' ? 'ts' : 'js'}`)
        imports(s.content, arquivo, rel)
      } catch (e) {
        const msg = e && e.message ? e.message.split('\n')[0] : String(e)
        if (/Failed to resolve/.test(msg)) {
          // Tipo vindo de pacote/arquivo que o build resolve de outro jeito
          // (não é erro de sintaxe): confere o script cru.
          for (const b of [descriptor.script, descriptor.scriptSetup]) {
            if (!b) continue
            sintaxe(b.content, `${rel}.${(b.lang || 'js') === 'ts' ? 'ts' : 'js'}`)
            imports(b.content, arquivo, rel)
          }
        } else {
          erros.push(`${rel}: script: ${msg}`)
        }
      }
    }
    if (descriptor.template) {
      const t = sfc.compileTemplate({
        source: descriptor.template.content,
        filename: arquivo,
        id: 'conferir',
        compilerOptions: { expressionPlugins: lang === 'ts' ? ['typescript'] : [], bindingMetadata: bindings },
      })
      for (const e of t.errors) erros.push(`${rel}: template: ${typeof e === 'string' ? e : e.message}`)
    }
  } else if (/\.(ts|js|mjs|cjs)$/.test(rel)) {
    sintaxe(codigo, rel)
    imports(codigo, arquivo, rel)
  }
}

if (erros.length) {
  console.log(erros.slice(0, 40).join('\n'))
  console.log(`a tela NÃO compila: ${erros.length} erro(s)`)
  process.exit(1)
}
console.log(`a tela compila: ok (${conferidos} arquivo(s))`)
