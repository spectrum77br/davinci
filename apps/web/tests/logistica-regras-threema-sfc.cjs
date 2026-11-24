// node tests/logistica-regras-threema-sfc.cjs — a Mensagem Threema das regras
// da aba Status da Logística em Cadastros › Envios Threema (tarefa 16, F4/C11,
// item 1 com escopo por regra). Sem rede e sem dado real:
//  - lib/enviosThreema: o link da regra (?envio=logistica.mensagem_regra&
//    regra=<id>), quem recebe a regra (a lista própria ou a padrão) e o
//    "vai para…" do botão Enviar;
//  - components/ThreemaRegraQuemRecebe.vue: as etiquetas de LEITURA no lugar
//    do 👤 — só nomes, "(padrão)" quando a regra usa a lista padrão,
//    "ninguém recebe" em vermelho, quem ficou de fora no title; o link para a
//    regra em Cadastros › Envios Threema só para admin ("só ADM altera quem
//    recebe", decisão do dono em 23/11/2026); os demais (churchill, israel,
//    londres, cairo sa e marrocos, com logistica:edit) leem a frase;
//  - pages/logistica.vue: o 👤 virou as etiquetas (na tabela e nos cartões do
//    celular, para quem vê a aba); o Enviar continua só para logistica:edit;
//    o modal de envio mostra "vai para…" sem escolha avulsa e o POST não
//    manda destinatários; nada grava mais o 👤 (PATCH nem seletor do .env);
//    o formulário de regra nova diz que vale a lista padrão.
//  - revisão F4: lista da regra sem ninguém válido diz "lista padrão" (é ela
//    que recebe), e a configuração antiga (emergência ou sem semente) aparece
//    como "configuração antiga" / "👤 de antes", sem mandar escolher no
//    Cadastro quando ninguém recebe.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}

const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const libs = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return Vue
  const m = /^~\/lib\/(.+)$/.exec(nome)
  if (!m) return require(nome)
  if (!libs.has(m[1])) {
    const exp = {}
    const fonte = fs.readFileSync(path.resolve(__dirname, '../lib', `${m[1]}.ts`), 'utf8')
    new Function('exports', 'require', transpile(fonte))(exp, requerer)
    libs.set(m[1], exp)
  }
  return libs.get(m[1])
}
const lib = requerer('~/lib/enviosThreema')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')

const NUXT_LINK = {
  props: ['to'],
  setup(p, { slots, attrs }) { return () => Vue.h('a', { href: p.to, title: attrs.title, 'data-editar': attrs['data-editar'] }, slots.default?.()) },
}

// O componente de verdade, no SSR (useIsAdmin é auto-import do Nuxt).
async function renderizar(props, { admin = true } = {}) {
  const rel = '../components/ThreemaRegraQuemRecebe.vue'
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: 'regra', inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'regra', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const g = { useIsAdmin: () => Vue.computed(() => admin) }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...Object.keys(g), transpile(script.content))(requerer, mod, mod.exports, ...Object.values(g))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = { ...mod.exports.default, render: tmod.exports.render }
  const app = Vue.createSSRApp({ render: () => Vue.h(comp, props) })
  app.config.warnHandler = () => {}
  app.component('NuxtLink', NUXT_LINK)
  return semComentarios(await renderToString(app))
}

const REGRA = '0b8d3c1e-1111-4222-8333-444455556666'
const da = (extra = {}) => ({ propria: true, ativo: true, nomes: ['joana dark', 'harry potter'], ninguem: false, avisos: [], ...extra })
const todas = (extra = {}) => ({
  chave: 'logistica.mensagem_regra', nome: 'Mensagem da regra de status', migrado: true, via: 'cadastro',
  usa_configuracao_antiga: false,
  padrao: da({ propria: false, nomes: ['churchill', 'londres', 'cairo sa'] }),
  regras: { [REGRA]: da() },
  ...extra,
})

;(async () => {
  // ---------------------------------------------------------------- lib
  {
    assert.equal(lib.CHAVE_MENSAGEM_REGRA, 'logistica.mensagem_regra')
    assert.equal(lib.linkDaRegra(REGRA), `/envios-threema?envio=logistica.mensagem_regra&regra=${REGRA}`)
    assert.equal(lib.linkDaRegra('a b&c'), '/envios-threema?envio=logistica.mensagem_regra&regra=a%20b%26c')
    assert.deepEqual(lib.quemRecebeDaRegra(todas(), REGRA), da(), 'a lista própria da regra')
    assert.deepEqual(lib.quemRecebeDaRegra(todas(), 'outra'), todas().padrao, 'sem lista própria: a padrão')
    assert.equal(lib.quemRecebeDaRegra(null, REGRA), null, 'não carregou')
    assert.deepEqual(lib.textoVaiPara(da()), { tipo: 'ok', texto: 'Vai para: joana dark, harry potter (lista própria da regra).' })
    assert.deepEqual(lib.textoVaiPara(todas().padrao), { tipo: 'ok', texto: 'Vai para: churchill, londres, cairo sa (lista padrão).' })
    assert.equal(lib.textoVaiPara(da({ ninguem: true, nomes: [] })).tipo, 'ninguem')
    assert.match(lib.textoVaiPara(da({ nomes: [] })).texto, /^Ninguém recebe: esta mensagem não sai\./)
    assert.equal(lib.textoVaiPara(null).tipo, 'desconhecido', 'sem a lista quem decide é o servidor')
    // revisão F4: a lista da regra sem ninguém válido → quem recebe é a padrão
    const caiu = da({ propria: false, lista_da_regra_sem_ninguem: true, nomes: ['churchill', 'londres'], avisos: ['cairo sa está suspenso', 'ninguém válido na lista da regra: vale a lista padrão do envio'] })
    assert.deepEqual(lib.textoVaiPara(caiu), { tipo: 'ok', texto: 'Vai para: churchill, londres (lista padrão: ninguém válido na lista da regra).' })
    assert.ok(!/lista própria/.test(lib.textoVaiPara(caiu).texto))
    // revisão F4: a configuração antiga (emergência ou sem semente)
    assert.deepEqual(lib.textoVaiPara(da({ antiga: true, nomes: ['joana'] })),
      { tipo: 'ok', texto: 'Vai para: joana (👤 de antes da regra, configuração antiga).' })
    assert.deepEqual(lib.textoVaiPara(da({ antiga: true, propria: false, nomes: ['londres'] })),
      { tipo: 'ok', texto: 'Vai para: londres (lista do servidor, configuração antiga).' })
    const antigaNinguem = lib.textoVaiPara(da({ antiga: true, propria: false, ninguem: true, nomes: [] }))
    assert.equal(antigaNinguem.tipo, 'ninguem')
    assert.match(antigaNinguem.texto, /pela configuração antiga \(o 👤 da regra e a lista do servidor estão vazios\)/)
    assert.ok(!/Cadastros › Envios Threema/.test(antigaNinguem.texto), 'na configuração antiga o Cadastro não resolve')
    assert.match(lib.AVISO_CONFIGURACAO_ANTIGA_DA_REGRA, /^Usando a configuração antiga/)
  }

  // ---------------------------------------------------------------- ThreemaRegraQuemRecebe: estático
  {
    const { fonte } = sfc('../components/ThreemaRegraQuemRecebe.vue')
    assert.match(fonte, /<NuxtLink\s+v-if="isAdmin"\s+:to="link"/, 'o link de editar só para admin')
    assert.ok(!/threema_id|threema_recipients|useApi|PUT|PATCH|POST/.test(fonte), 'só lê nomes; não edita nada')
    assert.ok(!/localStorage|sessionStorage/.test(fonte))
  }

  // ---------------------------------------------------------------- admin: etiquetas + link da regra
  {
    const html = await renderizar({ regraId: REGRA, quem: da({ avisos: ['sumido está suspenso'] }) })
    assert.match(html, new RegExp(`data-regra-quem-recebe="${REGRA}"`))
    const etiquetas = [...html.matchAll(/<span[^>]*data-etiqueta[^>]*>([^<]*)<\/span>/g)].map((m) => m[1])
    assert.deepEqual(etiquetas, ['joana dark', 'harry potter'])
    assert.ok(!/data-padrao/.test(html), 'lista própria: sem "(padrão)"')
    assert.match(html, /data-avisos/, 'quem ficou de fora: o sinal no lugar, a frase no title')
    assert.match(html, /title="Recebem: joana dark, harry potter \(lista própria da regra\)\.\nsumido está suspenso\nQuem recebe se escolhe em Cadastros › Envios Threema\."/)
    assert.match(html, new RegExp(`<a href="/envios-threema\\?envio=logistica\\.mensagem_regra&amp;regra=${REGRA}"`))
    assert.ok(!/TST[A-Z0-9]{5}/.test(html), 'nunca o ID do Threema')
  }

  // ---------------------------------------------------------------- padrão, não-admin e ninguém
  {
    let html = await renderizar({ regraId: REGRA, quem: todas().padrao }, { admin: false })
    assert.match(html, /<span[^>]*data-padrao[^>]*>\(padrão\)<\/span>/)
    assert.ok(!/href=/.test(html), 'não-admin: sem link de editar')
    assert.match(html, /Só admin muda quem recebe \(Cadastros › Envios Threema\)\./)
    html = await renderizar({ regraId: REGRA, quem: da({ ninguem: true, nomes: [] }) })
    assert.match(html, /class="[^"]*text-red-700[^"]*"[^>]*data-situacao="ninguem"[^>]*>ninguém recebe</)
    assert.ok(!/data-etiqueta/.test(html))
    html = await renderizar({ regraId: REGRA, quem: null }, { admin: false })
    assert.match(html, />—</, 'não carregou: só o traço')
  }

  // ---------------------------------------------------------------- revisão F4: lista da regra sem ninguém válido
  {
    const caiu = da({ propria: false, lista_da_regra_sem_ninguem: true, nomes: ['churchill', 'londres'], avisos: ['ninguém válido na lista da regra: vale a lista padrão do envio'] })
    const html = await renderizar({ regraId: REGRA, quem: caiu })
    const etiquetas = [...html.matchAll(/<span[^>]*data-etiqueta[^>]*>([^<]*)<\/span>/g)].map((m) => m[1])
    assert.deepEqual(etiquetas, ['churchill', 'londres'])
    assert.match(html, /<span[^>]*data-padrao[^>]*>\(padrão\)<\/span>/, 'quem recebe é a padrão')
    assert.match(html, /title="Recebem: churchill, londres \(lista padrão: ninguém válido na lista da regra\)\.\nninguém válido na lista da regra: vale a lista padrão do envio\n/)
    assert.ok(!/lista própria/.test(html))
    assert.ok(!/data-antiga/.test(html))
  }

  // ---------------------------------------------------------------- revisão F4: configuração antiga
  {
    let html = await renderizar({ regraId: REGRA, quem: da({ antiga: true, nomes: ['joana'] }) })
    assert.match(html, /<span[^>]*data-antiga[^>]*>configuração antiga<\/span>/, 'o selo âmbar')
    assert.match(html, /class="[^"]*text-amber-800[^"]*"[^>]*data-antiga/)
    assert.match(html, /<span[^>]*data-pessoa-antiga[^>]*>\(👤 de antes\)<\/span>/)
    assert.match(html, /title="Recebem: joana \(👤 de antes da regra, configuração antiga\)\.\nUsando a configuração antiga/)
    assert.ok(!/lista própria/.test(html), 'o 👤 congelado não é "lista própria"')
    html = await renderizar({ regraId: REGRA, quem: da({ antiga: true, propria: false, nomes: ['londres'] }) }, { admin: false })
    assert.match(html, /data-padrao/)
    assert.match(html, /data-antiga/)
    assert.match(html, /\(lista do servidor, configuração antiga\)/)
    html = await renderizar({ regraId: REGRA, quem: da({ antiga: true, propria: false, ninguem: true, nomes: [] }) })
    assert.match(html, /data-situacao="ninguem"/)
    assert.match(html, /title="Ninguém recebe esta mensagem pela configuração antiga/)
    // fora da configuração antiga: sem selo
    html = await renderizar({ regraId: REGRA, quem: da() })
    assert.ok(!/data-antiga|data-pessoa-antiga|configuração antiga/.test(html))
  }

  // ---------------------------------------------------------------- pages/logistica.vue
  {
    const { descriptor, fonte } = sfc('../pages/logistica.vue')
    const tpl = descriptor.template.content
    const script = descriptor.scriptSetup.content
    // o 👤 de editar sumiu: nada escolhe nem grava quem recebe aqui
    for (const velho of [
      'escolherDestinatarios', 'threemaDestinatarios', 'toggleThreemaDest', 'preselecionarThreema',
      '/api/logistica/threema/destinatarios', 'threemaModal.selecionados', "mode: 'enviar'", "'salvar'",
    ]) {
      assert.ok(!fonte.includes(velho), `logistica.vue ainda tem ${velho}`)
    }
    assert.ok(!/\{\s*threema_recipients:/.test(fonte), 'nenhum PATCH/POST grava o 👤')
    assert.equal((fonte.match(/threema_recipients/g) || []).length, 1, 'só o tipo (o 👤 congelado)')
    // as etiquetas de leitura, na tabela e nos cartões, para quem vê a aba
    const usos = [...tpl.matchAll(/<ThreemaRegraQuemRecebe :regra-id="s\.id" :quem="quemRecebeDaRegra\(threemaRegras, s\.id\)" \/>/g)]
    assert.equal(usos.length, 2, 'tabela e cartões do celular')
    assert.equal((tpl.match(/<div v-if="s\.mensagem_threema" class="mt-1 flex flex-wrap items-center gap-1"/g) || []).length, 2,
      'as etiquetas não dependem de logistica:edit')
    // o Enviar continua só para quem edita a Logística
    assert.equal((tpl.match(/v-if="canEdit"\s+class="inline-flex items-center gap-1 rounded border px-[12](\.5)? py-[01](\.5)? text-(\[11px\]|xs) text-primary[^"]*"[\s\S]{0,200}?enviarThreema\(s\)/g) || []).length, 2)
    // quem recebe vem da rota leve, por regra, e é recarregada com a aba e ao abrir o modal
    assert.ok(script.includes('`/api/threema/envios/${CHAVE_MENSAGEM_REGRA}/quem-recebe/regras`'))
    assert.match(script, /async function refreshStatus\(\) \{[\s\S]*?void loadThreemaRegras\(\)/)
    assert.match(script, /async function enviarThreema\(s: LogisticaStatus\) \{[\s\S]*?await loadThreemaRegras\(\)/)
    // o envio: sem corpo (sem escolha avulsa)
    const envio = /`\/api\/logistica\/status\/\$\{s\.id\}\/enviar-threema`,\s*\{([^}]*)\}/.exec(script)
    assert.ok(envio, 'o POST do botão da regra')
    assert.equal(envio[1].trim(), "method: 'POST'", 'sem recipients')
    assert.ok(!/recipients/.test(script.replace(/threema_recipients/g, '')), 'nenhum destinatário escolhido na tela')
    // o modal: "vai para…" sem lista de marcar, e não manda quando ninguém recebe
    const modal = tpl.slice(tpl.indexOf('<!-- Modal: enviar a Mensagem Threema da regra.'), tpl.indexOf('<!-- Modal create/edit caso -->'))
    assert.ok(modal.length > 100)
    assert.ok(!/type="checkbox"/.test(modal), 'sem escolha avulsa')
    assert.match(modal, /\{\{ threemaVaiPara\.texto \}\}/)
    assert.match(modal, /:disabled="threemaModal\.sending \|\| threemaVaiPara\?\.tipo === 'ninguem'"/)
    assert.match(modal, /<NuxtLink\s+v-if="threemaModal\.status"\s+:to="linkDaRegra\(threemaModal\.status\.id\)"/)
    assert.match(modal, /<template v-if="isAdmin">/, 'o link só para admin')
    assert.match(modal, /Só admin muda quem recebe/)
    // revisão F4: na configuração antiga o modal avisa (mexer no Cadastro não muda o envio)
    assert.match(modal, /<p\s+v-if="threemaQuemDoModal\?\.antiga"[\s\S]*?data-configuracao-antiga\s*>\s*\{\{ AVISO_CONFIGURACAO_ANTIGA_DA_REGRA \}\}/)
    assert.match(script, /const threemaQuemDoModal = computed\(\(\) =>\s*threemaModal\.status \? quemRecebeDaRegra\(threemaRegras\.value, threemaModal\.status\.id\) : null,/)
    assert.match(script, /const threemaVaiPara = computed\(\(\) =>\s*threemaModal\.status \? textoVaiPara\(threemaQuemDoModal\.value\) : null,/)
    // a regra nova: vale a lista padrão
    assert.match(tpl, /data-quem-recebe-nova>\s*Vai para a lista padrão da Mensagem da regra\./)
    // o servidor diz por que não mandou (ninguém recebe)
    assert.match(script, /function erroThreema\(e: any\): string \{[\s\S]*?d\?\.message \|\| d\?\.code/)
  }

  console.log('ok — logistica-regras-threema-sfc')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
