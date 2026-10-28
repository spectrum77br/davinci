// node tests/email-padroes-sfc.cjs — compilação e fluxos dos e-mails do Tuta por marca.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/email-padroes.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'marca-emails-check' })
assert.deepEqual(template.errors, [])
compileScript(descriptor, { id: 'marca-emails-check' })
// A matriz de assinaturas por canal saiu (05/10); voltou só a assinatura pra colar no Tuta (07/10).
assert.doesNotMatch(descriptor.template.content, /Configurar|iframe/)
assert.doesNotMatch(descriptor.scriptSetup.content, /email-assinaturas/)
assert.match(descriptor.template.content, /Copiar assinatura/)
// 28/10/2026: só SAC e Admin; a assinatura vem da API (a mesma da resposta do
// /atendimento) — a montagem saiu do front.
assert.equal(fs.existsSync(path.resolve(__dirname, '../lib/assinaturaEmail.ts')), false)
assert.doesNotMatch(descriptor.template.content + descriptor.scriptSetup.content, /duvidas@|atacado@|Dúvidas|Atacado/)
const script = descriptor.scriptSetup.content.replace(/^import.*$/gm, '')
const transpiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const factory = new Function('ref', 'computed', 'onMounted', 'definePageMeta', 'useCan', 'useApi',
  'apiErrMsg', 'MARCAS_ERROS', 'MARCA_EMAIL_TIPO_LABELS', 'navigator', 'confirm', 'setTimeout',
  'nextTick', 'watch', 'ClipboardItem', 'Blob', 'clearTimeout', 'URLSearchParams',
  transpiled + '\nreturn {grid,rascunhos,error,success,load,salvar,preencher,podePreencher,sujo,sugestao,dominio,copiar,copiado,' +
  'sigMarcaId,sigTipo,sigEmail,sigFone,sigRow,assinaturaHtml,assinaturaTexto,sigCarregando,sigErro,copiarAssinatura,sigAviso};')
const marca = (id, nome, site, extra = {}) => ({
  id, nome, slug: nome, ativo: true, site, sac_fone: null, has_logo: false, updated_at: '2026-10-05',
  nome_exibicao: nome[0].toUpperCase() + nome.slice(1), cor: '#123456', logo_publico: false, fone_formatado: '', ...extra,
})
function page({ edit = true, fail = false, confirma = true, assinaturaFalha = false, adiar = false } = {}) {
  const calls = [], copias = [], timers = [], adiados = []
  const api = async (url, opts = {}) => {
    calls.push({ url, opts })
    if (url === '/api/marca-emails') return {
      tipos: ['sac', 'admin'],
      rows: [
        { marca: marca('m1', 'uranyx', 'https://www.uranyx.com.br/', { sac_fone: '11983517003', fone_formatado: '+55 11 98351-7003', logo_publico: true, cor: '#F47B42' }), emails: { sac: 'sac@uranyx.com.br', admin: null } },
        { marca: marca('m2', 'locagil', null), emails: { sac: null, admin: null } },
      ],
    }
    const a = url.match(/^\/api\/marca-emails\/(m\d)\/assinatura\?(.*)$/)
    if (a) {
      const q = new URLSearchParams(a[2])
      const out = { html: `<p>${a[1]}|${q.get('tipo')}|${q.get('email')}|${q.get('fone')}</p>`, texto: `${a[1]} ${q.get('tipo')}` }
      // `adiar`: a resposta só chega quando o teste mandar (pedidos sobrepostos).
      if (adiar) return new Promise((ok, falha) => adiados.push({ ok: () => ok(out), falha: () => falha(new Error('Sem rede')) }))
      if (assinaturaFalha) throw new Error('Sem rede')
      return out
    }
    if (opts.method === 'PUT') {
      if (fail) throw new Error('Falha ao salvar')
      return { marca: marca(url.split('/').pop(), 'x', null), emails: { ...opts.body } }
    }
    throw new Error(`Chamada inesperada: ${url}`)
  }
  // setTimeout guardado: `rodar()` dispara os pendentes (a prévia espera 250 ms parada).
  const setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length }
  const clearTimeout = id => { if (id && timers[id - 1]) timers[id - 1].fn = null }
  const rodar = async () => {
    await Vue.nextTick()
    for (const t of timers.splice(0)) if (t.fn) await t.fn()
    await Vue.nextTick()
  }
  const navigator = { clipboard: { writeText: async t => { copias.push(t) }, write: async items => { copias.push(items[0]) } } }
  class ClipboardItem { constructor(d) { this.data = d } }
  class Blob { constructor(parts, o) { this.text = parts.join(''); this.type = o.type } }
  const p = factory(Vue.ref, Vue.computed, () => {}, () => {}, () => Vue.ref(edit), () => ({ api }),
    e => e.message, {}, { sac: 'SAC', admin: 'Admin' }, navigator, () => confirma, setTimeout,
    Vue.nextTick, Vue.watch, ClipboardItem, Blob, clearTimeout, URLSearchParams)
  // Dispara os timers SEM esperar a resposta (o pedido fica em curso).
  const disparar = async () => {
    await Vue.nextTick()
    for (const t of timers.splice(0)) if (t.fn) t.fn()
    await Vue.nextTick()
  }
  return { p, calls, copias, rodar, disparar, adiados }
}
;(async () => {
  {
    const { p, calls } = page()
    await p.load()
    const [uranyx, locagil] = p.grid.value.rows
    assert.equal(p.dominio('https://www.uranyx.com.br/'), 'uranyx.com.br')
    assert.equal(p.sugestao(uranyx.marca, 'admin'), 'admin@uranyx.com.br')
    assert.equal(p.rascunhos.value.m1.sac, 'sac@uranyx.com.br')
    assert.equal(p.rascunhos.value.m1.admin, '')
    assert.equal(p.sujo(uranyx), false)
    // "padrão" só preenche os vazios e só quando a marca tem site.
    assert.equal(p.podePreencher(uranyx), true)
    assert.equal(p.podePreencher(locagil), false)
    p.rascunhos.value.m1.sac = 'contato@uranyx.com.br'
    p.preencher(uranyx)
    assert.deepEqual({ ...p.rascunhos.value.m1 }, { sac: 'contato@uranyx.com.br', admin: 'admin@uranyx.com.br' })
    assert.equal(p.sujo(uranyx), true)
    p.rascunhos.value.m1.sac = ' SAC@Uranyx.com.br '
    await p.salvar(uranyx)
    const put = calls.find(c => c.opts.method === 'PUT')
    assert.equal(put.url, '/api/marca-emails/m1')
    assert.deepEqual(put.opts.body, { sac: 'sac@uranyx.com.br', admin: 'admin@uranyx.com.br' })
    assert.equal(uranyx.emails.admin, 'admin@uranyx.com.br')
    assert.equal(p.sujo(uranyx), false)
    assert.match(p.success.value, /uranyx/)
    // Linha sem mudança não salva.
    await p.salvar(locagil)
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 1)
  }
  {
    const { p, calls } = page({ edit: false })
    await p.load()
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.salvar(p.grid.value.rows[1])
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 0)
  }
  {
    const { p } = page({ fail: true })
    await p.load()
    const row = p.grid.value.rows[1]
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.salvar(row)
    assert.equal(p.rascunhos.value.m2.sac, 'sac@locagil.com.br')
    assert.equal(row.emails.sac, null)
    assert.match(p.error.value, /locagil: Falha ao salvar/)
  }
  {
    const { p, calls } = page({ confirma: false })
    await p.load()
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.load()
    assert.equal(calls.filter(c => c.url === '/api/marca-emails').length, 1)
    assert.equal(p.rascunhos.value.m2.sac, 'sac@locagil.com.br')
  }
  {
    const { p, copias } = page()
    await p.load()
    await p.copiar('sac@uranyx.com.br')
    assert.deepEqual(copias, ['sac@uranyx.com.br'])
    assert.equal(p.copiado.value, 'sac@uranyx.com.br')
  }
  {
    // Assinatura: a API monta (marca, caixa, e-mail e WhatsApp da tela); a tela
    // espera parar de digitar, e só a última resposta vale.
    const { p, copias, calls, rodar } = page()
    await p.load()
    await rodar()
    assert.equal(p.sigRow.value.marca.id, 'm1')
    assert.equal(p.sigEmail.value, 'sac@uranyx.com.br')
    assert.equal(p.sigFone.value, '+55 11 98351-7003')
    assert.equal(p.assinaturaHtml.value, '<p>m1|sac|sac@uranyx.com.br|+55 11 98351-7003</p>')
    assert.equal(p.sigCarregando.value, false)
    // Admin: o e-mail da caixa vira admin@ (a sugestão do site) e a prévia segue.
    p.sigTipo.value = 'admin'
    await Vue.nextTick()
    assert.equal(p.sigEmail.value, 'admin@uranyx.com.br')
    assert.equal(p.sigCarregando.value, true)
    // Atualizando: o copiar não copia a de antes.
    await p.copiarAssinatura()
    assert.equal(copias.length, 0)
    await rodar()
    assert.equal(p.assinaturaHtml.value, '<p>m1|admin|admin@uranyx.com.br|+55 11 98351-7003</p>')
    // Digitar rápido = um pedido só (o último).
    const antes = calls.filter(c => c.url.includes('/assinatura')).length
    p.sigFone.value = '+55 11 9'
    await Vue.nextTick()
    p.sigFone.value = '+55 11 90000-0000'
    await rodar()
    assert.equal(calls.filter(c => c.url.includes('/assinatura')).length, antes + 1)
    assert.match(p.assinaturaHtml.value, /\+55 11 90000-0000/)
    // Outra marca: o e-mail e o fone voltam ao cadastro dela.
    p.sigMarcaId.value = 'm2'
    await rodar()
    assert.equal(p.assinaturaHtml.value, '<p>m2|admin|admin@marca.com.br|</p>')
    await p.copiarAssinatura()
    const item = copias.at(-1)
    assert.equal(item.data['text/html'].type, 'text/html')
    assert.equal(item.data['text/html'].text, p.assinaturaHtml.value)
    assert.equal(item.data['text/plain'].text, 'm2 admin')
    assert.match(p.sigAviso.value, /copiada/)
  }
  {
    // Pedidos sobrepostos: a resposta velha que chega depois de uma mudança
    // nova não aparece nem libera o copiar; a falha do último limpa a prévia.
    const { p, copias, disparar, adiados } = page({ adiar: true })
    await p.load()
    await disparar()
    adiados.shift().ok()
    await Vue.nextTick(); await Vue.nextTick()
    assert.equal(p.assinaturaHtml.value, '<p>m1|sac|sac@uranyx.com.br|+55 11 98351-7003</p>')
    assert.equal(p.sigCarregando.value, false)
    // Pedido B em curso; a pessoa digita de novo (timer C pendente); B chega.
    p.sigEmail.value = 'sac@uranyx.com.b'
    await disparar()
    p.sigEmail.value = 'sac@uranyx.com.br2'
    await Vue.nextTick()
    adiados.shift().ok()
    await Vue.nextTick(); await Vue.nextTick()
    assert.equal(p.assinaturaHtml.value, '<p>m1|sac|sac@uranyx.com.br|+55 11 98351-7003</p>', 'a resposta de B não aparece')
    assert.equal(p.sigCarregando.value, true, 'C ainda vem: o copiar continua desligado')
    await p.copiarAssinatura()
    assert.equal(copias.length, 0)
    // C falha: a prévia some (nunca fica a de antes para copiar).
    await disparar()
    adiados.shift().falha()
    await Vue.nextTick(); await Vue.nextTick()
    assert.equal(p.assinaturaHtml.value, '')
    assert.match(p.sigErro.value, /Sem rede/)
    assert.equal(p.sigCarregando.value, false)
    await p.copiarAssinatura()
    assert.equal(copias.length, 0)
    // Mudar de novo apaga o erro.
    p.sigTipo.value = 'admin'
    await Vue.nextTick()
    assert.equal(p.sigErro.value, '')
  }
  {
    // "recarregar" refaz a prévia: a que falhou tenta de novo.
    const { p, calls, adiados, disparar } = page({ adiar: true })
    await p.load()
    await disparar()
    adiados.shift().falha()
    await Vue.nextTick(); await Vue.nextTick()
    assert.match(p.sigErro.value, /Sem rede/)
    await p.load()
    await disparar()
    adiados.shift().ok()
    await Vue.nextTick(); await Vue.nextTick()
    assert.equal(p.sigErro.value, '')
    assert.equal(p.assinaturaHtml.value, '<p>m1|sac|sac@uranyx.com.br|+55 11 98351-7003</p>')
    assert.equal(calls.filter(c => c.url.includes('/assinatura')).length, 2)
  }
  {
    // A API falhou: a mensagem aparece e o copiar não copia nada.
    const { p, copias, rodar } = page({ assinaturaFalha: true })
    await p.load()
    await rodar()
    assert.equal(p.assinaturaHtml.value, '')
    assert.match(p.sigErro.value, /Sem rede/)
    await p.copiarAssinatura()
    assert.equal(copias.length, 0)
  }
  console.log('email-padroes-sfc: ok')
})().catch(e => { console.error(e); process.exit(1) })
