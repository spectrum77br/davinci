// node tests/transferir-estoque-modal.cjs
// Controle de Estoque › "Transferir estoque" (05/11/2026): roda o <script setup>
// REAL do TransferirEstoqueModal com o Vue de verdade.
//  * SKU vai minúsculo e sem espaço; só transfere depois de "Conferir" sem erro;
//  * mexer numa linha depois de conferir pede para conferir de novo;
//  * a chave Idempotency-Key é a mesma num reenvio (erro de rede) e troca só
//    depois de "Nova transferência";
//  * saldo mudou entre Conferir e Transferir (422) → mostra o motivo na linha;
//  * resolver linha "sem confirmação" manda conferi_extrato.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')
const { ref, computed, watch, nextTick } = require('vue')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const file = path.join(__dirname, '../components/TransferirEstoqueModal.vue')
const sfc = parse(fs.readFileSync(file, 'utf8'), { filename: file })
assert.deepEqual(sfc.errors, [])
const src = sfc.descriptor.scriptSetup.content
const ast = ts.createSourceFile('m.ts', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

// Tudo menos imports, tipos, defineProps/defineEmits e useApi (vêm de fora).
const corpo = ast.statements
  .filter((n) => !ts.isImportDeclaration(n) && !ts.isTypeAliasDeclaration(n))
  .map((n) => src.slice(n.getStart(ast), n.getEnd()))
  .filter((t) => !/defineProps|defineEmits|useApi\(\)/.test(t))
  .join('\n')

function montar(respostas) {
  const chamadas = []
  const emitidos = []
  const props = { open: false }
  const api = async (url, opts = {}) => {
    chamadas.push({ url, ...opts })
    const r = respostas(url, opts, chamadas.length)
    if (r instanceof Error) throw r
    return r
  }
  const fn = new Function(
    'ref', 'computed', 'watch', 'onBeforeUnmount', 'props', 'emit', 'api', 'confirm',
    transpile(`${corpo}\nreturn { linhas, observacao, idemKey, conferidas, erro, feita, historico, payloadLinhas, podeTransferir, motivoDesligado, limpar, conferir, transferir, resolver, novaTransferencia }`),
  )
  const vm = fn(ref, computed, watch, () => {}, props, (e) => emitidos.push(e), api, () => true)
  return { vm, chamadas, emitidos }
}

const conferidaOk = (o, d, q) => ({
  ordem: 0, origem: o, destino: d, quantidade: q, origem_nome: 'X', destino_nome: 'X',
  saldo_fisico: 10, saldo_virtual: 10, troca_produto: false, custo: 65, erros: [],
})

async function main() {
  // 1) Normaliza, só transfere depois de conferir, edição invalida a conferência.
  {
    const { vm, chamadas } = montar((url) => {
      if (url.endsWith('/conferir')) return { linhas: [conferidaOk('dg010.ra', 'dg010.ci', 3)] }
      return []
    })
    vm.limpar()
    assert.equal(vm.linhas.value.length, 1)
    assert.ok(vm.idemKey.value.length >= 8)
    vm.linhas.value[0].origem = ' DG010.RA '
    vm.linhas.value[0].destino = 'dg010.CI'
    vm.linhas.value[0].quantidade = 3
    assert.deepEqual(vm.payloadLinhas.value, [{ origem: 'dg010.ra', destino: 'dg010.ci', quantidade: 3 }])
    assert.equal(vm.podeTransferir.value, false)
    await vm.conferir()
    assert.equal(chamadas.at(-1).method, 'POST')
    assert.deepEqual(chamadas.at(-1).body, { linhas: vm.payloadLinhas.value })
    assert.equal(vm.podeTransferir.value, true)
    vm.linhas.value[0].quantidade = 4
    await nextTick()
    assert.equal(vm.conferidas.value, null)
    assert.equal(vm.podeTransferir.value, false)
  }

  // 2) Mesma chave no reenvio depois de erro de rede; nova só em "Nova transferência".
  {
    let falhar = true
    const { vm, chamadas, emitidos } = montar((url, opts) => {
      if (url.endsWith('/conferir')) return { linhas: [conferidaOk('a004.cd', 'a004.pi', 200)] }
      if (url === '/api/estoque/transferencias' && opts.method === 'POST') {
        if (falhar) { falhar = false; return new Error('rede caiu') }
        return { id: 't1', numero: 7, criado_em: '', criado_por: null, observacao: null, estado: 'ok', linhas: [] }
      }
      return []
    })
    vm.limpar()
    Object.assign(vm.linhas.value[0], { origem: 'a004.cd', destino: 'a004.pi', quantidade: 200 })
    await nextTick()
    await vm.conferir()
    const chave = vm.idemKey.value
    await vm.transferir()
    assert.equal(vm.feita.value, null)
    assert.equal(vm.erro.value, 'rede caiu')
    await vm.transferir()
    const posts = chamadas.filter((c) => c.url === '/api/estoque/transferencias' && c.method === 'POST')
    assert.equal(posts.length, 2)
    assert.equal(posts[0].headers['Idempotency-Key'], chave)
    assert.equal(posts[1].headers['Idempotency-Key'], chave)
    assert.equal(vm.feita.value.numero, 7)
    assert.ok(emitidos.includes('transferido'))
    assert.equal(vm.podeTransferir.value, false) // já feita: não lança de novo
    vm.novaTransferencia()
    assert.notEqual(vm.idemKey.value, chave)
  }

  // 3) Saldo mudou entre Conferir e Transferir: o 422 volta para as linhas.
  {
    const { vm } = montar((url, opts) => {
      if (url.endsWith('/conferir')) return { linhas: [conferidaOk('a004.cd', 'a004.pi', 5)] }
      if (url === '/api/estoque/transferencias' && opts.method === 'POST') {
        const e = new Error('422')
        e.data = { detail: { code: 'transferencia_invalida', linhas: [{ ...conferidaOk('a004.cd', 'a004.pi', 5), erros: ['a004.cd tem 2 disponível no Bling e a transferência pede 5.'] }] } }
        return e
      }
      return []
    })
    vm.limpar()
    Object.assign(vm.linhas.value[0], { origem: 'a004.cd', destino: 'a004.pi', quantidade: 5 })
    await nextTick()
    await vm.conferir()
    await vm.transferir()
    assert.match(vm.conferidas.value[0].erros[0], /tem 2 disponível/)
    assert.equal(vm.podeTransferir.value, false)
  }

  // 4) Resolver manda a tentativa que a pessoa viu; "Já lancei no Bling"
  //    (entrada recusada, sem "conferir") também afirma o extrato.
  {
    const t = { id: 't9', numero: 9, criado_em: '', criado_por: null, observacao: null, estado: 'ok', linhas: [] }
    const { vm, chamadas } = montar(() => t)
    vm.historico.value = [{ ...t, estado: 'atencao' }]
    await vm.resolver({ id: 'L1', precisa_conferir: true, tentativa: '2026-11-05T12:00:00Z' }, 'ja_lancou')
    let c = chamadas.at(-1)
    assert.equal(c.url, '/api/estoque/transferencias/linhas/L1/resolver')
    assert.deepEqual(c.body, { acao: 'ja_lancou', conferi_extrato: true, tentativa: '2026-11-05T12:00:00Z' })
    assert.equal(vm.historico.value[0].estado, 'ok')
    await vm.resolver({ id: 'L2', precisa_conferir: false, tentativa: null }, 'ja_lancou')
    c = chamadas.at(-1)
    assert.deepEqual(c.body, { acao: 'ja_lancou', conferi_extrato: true, tentativa: null })
    await vm.resolver({ id: 'L3', precisa_conferir: false, tentativa: 'x' }, 'tentar_de_novo')
    c = chamadas.at(-1)
    assert.deepEqual(c.body, { acao: 'tentar_de_novo', conferi_extrato: false, tentativa: 'x' })
  }

  // 5) Linha em branco no meio sai antes de conferir; quantidade quebrada
  //    para na tela; mexer durante o Conferir não deixa transferir.
  {
    let mexer = null
    const { vm, chamadas } = montar((url, opts) => {
      if (url.endsWith('/conferir')) {
        if (mexer) mexer()
        return { linhas: opts.body.linhas.map((l) => conferidaOk(l.origem, l.destino, l.quantidade)) }
      }
      return []
    })
    vm.limpar()
    vm.linhas.value = [
      { origem: 'a004.cd', destino: 'a004.pi', quantidade: 2 },
      { origem: '', destino: '', quantidade: null },
      { origem: 'dg107.ci', destino: 'dg108.ci', quantidade: 1 },
    ]
    await nextTick()
    await vm.conferir()
    assert.equal(vm.linhas.value.length, 2)
    assert.equal(vm.conferidas.value[1].origem, 'dg107.ci')
    vm.linhas.value[0].quantidade = 1.5
    await nextTick()
    const antes = chamadas.length
    await vm.conferir()
    assert.equal(chamadas.length, antes)
    assert.match(vm.erro.value, /inteiro/)
    assert.equal(vm.motivoDesligado.value, 'Clique em "Conferir" antes de transferir.')
    vm.linhas.value[0].quantidade = 2
    mexer = () => { vm.linhas.value[0].quantidade = 3 }
    await nextTick()
    await vm.conferir()
    await nextTick()
    assert.equal(vm.podeTransferir.value, false)
  }

  console.log('ok transferir-estoque-modal (5 casos)')
}

main().catch((e) => { console.error(e); process.exit(1) })
