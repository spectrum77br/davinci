// node tests/envios-threema-edicao-sfc.cjs — Cadastros › Envios Threema, edição (tarefa 16, C7).
// Sem rede e sem dado real:
//  - lib/enviosThreema: a confirmação ao desligar (com o que acontece com os
//    avisos), a do teste, o formato do Threema e o aviso de Usuários;
//  - a gaveta: envio do Mac editável (o seletor de Pessoas, Grupos e
//    Contatos externos; desligar pede confirmação; o PATCH só leva o que
//    mudou); envio do servidor não migrado só leitura, com "Mandar teste";
//    item 1 sem interruptor e com a lista de cada regra;
//  - a página: as abas Envios | Grupos | Contatos externos e o botão de
//    emergência;
//  - o seletor, os grupos (409 em uso) e os contatos (ID no formato);
//  - Usuários: o formato do Threema e o aviso antes de suspender/excluir.
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
let montadosAtual = []
const vueDoTeste = { ...Vue, onMounted: (fn) => montadosAtual.push(fn), onBeforeUnmount: () => {} }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return vueDoTeste
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
const esperar = () => new Promise((r) => setTimeout(r, 0))

// O setup de verdade fora de um app e o HTML de um app SSR com o MESMO estado.
// Um componente filho de verdade (template dentro do setup), para a gaveta
// mostrar o seletor.
function componente(rel) {
  const { descriptor } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: true })
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(script.content))(requerer, mod, mod.exports)
  return mod.exports.default
}
const SELETOR = componente('../components/EnviosThreemaSeletor.vue')

function montar(rel, { responder, props = {}, query = {}, user = { role: 'admin' }, componentes = { EnviosThreemaSeletor: SELETOR } }) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const montados = []
  montadosAtual = montados
  const route = Vue.reactive({ query: { ...query } })
  const emitidos = []
  const g = {
    definePageMeta: () => {},
    useRoute: () => route,
    useRouter: () => ({ replace: ({ query: q }) => { route.query = { ...q } } }),
    useAuthStore: () => ({ user }),
    useApi: () => ({
      api: async (url, opts = {}) => {
        chamadas.push({ url, method: opts.method || 'GET', query: { ...(opts.query || {}) }, body: opts.body })
        return structuredClone(await responder(url, opts))
      },
    }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const propsReativas = Vue.reactive({ ...props })
  const bruto = comp.setup(propsReativas, { emit: (n, ...a) => emitidos.push([n, ...a]), expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, { ...propsReativas }) })
    a.config.warnHandler = () => {}
    for (const [n, c] of Object.entries(componentes)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, route, props: propsReativas, emitidos, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const confirmacoes = []
let responderConfirm = true
globalThis.confirm = (t) => { confirmacoes.push(t); return responderConfirm }

// ---------------------------------------------------------------- dados falsos
const PESSOAS = {
  usuarios: [
    { id: 'u-h', nome: 'harry potter', email: 'h@x', admin: false, tem_threema: true, valido: true, aviso: null },
    { id: 'u-sem', nome: 'Sem Threema', email: 's@x', admin: false, tem_threema: false, valido: false, aviso: 'Sem Threema sem Threema' },
  ],
  grupos: [{ id: 'g-1', nome: 'Estoque e NF', descricao: null, membros: [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter', valido: true, aviso: null }], usado_em: [{ chave: 'devolucoes.codigos_ml_tuta', nome: 'Códigos' }] }],
  contatos: [
    { id: 'c-r', nome: 'Roma', threema_id: 'VB…', ativo: true, origem: 'migracao', observacao: null, usado_em: [], e_de_usuario: null },
    { id: 'c-x', nome: 'Antigo', threema_id: 'XX…', ativo: false, origem: 'tela', observacao: null, usado_em: [], e_de_usuario: null },
  ],
}
const BASE = {
  painel: 'Devoluções', aba: null, descricao: 'x', frequencia_tipo: 'horario', frequencia_texto: '08:00',
  remetente: null, gerenciado: true, migrado: false, fatia: 'mac', interruptor: 'proprio', ao_desligar: 'acumula',
  ao_desligar_texto: 'ao religar, os pendentes saem de uma vez', escopo_tipo: null, por_que_nao_edita: null,
  onde_configurar: '.env do Mac (DEVOL_CODIGOS_THREEMA_TO)', tem_reaviso: false, reaviso_horas: null, ativo: true,
  ativo_texto: 'ligado (segue o interruptor de hoje)', ativo_cadastro: null, ativo_alterado_por: null, ativo_alterado_em: null,
  observacao: null, semeado_em: null, depende_de: [], ultimo: null, situacao: 'mac_env', situacoes: ['mac_env'], faixas: [],
  destinatarios: [], antes: null, regras: [], emergencia: { usar_legado: false, alterado_por: null, alterado_em: null, env_desligado: false },
}
const MAC = {
  ...BASE, chave: 'devolucoes.codigos_ml_tuta', nome: 'Códigos de devolução do ML', onde_roda: 'mac', editavel: true, desligavel: true,
  quem_recebe: { via: 'mac_env', motivo: null, fonte: '.env do Mac (DEVOL_CODIGOS_THREEMA_TO)', nomes: [], avisos: [], total: 0 },
}
const SERVIDOR = {
  ...BASE, chave: 'logistica.informar', painel: 'Logística', nome: 'Informar da Logística', onde_roda: 'servidor', remetente: 'logistica',
  editavel: false, desligavel: true, fatia: 'F2', ao_desligar: 'recusa', ao_desligar_texto: 'o botão recusa o envio enquanto estiver desligado',
  por_que_nao_edita: 'Ainda usa a configuração antiga. Para mudar quem recebe: Logística › Informar.',
  quem_recebe: { via: 'legado', motivo: 'nao_migrado', fonte: "Informar 'logistica'", nomes: [{ nome: 'harry potter', tipo: 'usuario' }, { nome: 'heisenberg', tipo: 'usuario' }], avisos: [], total: 2 },
}
const ITEM1 = {
  ...SERVIDOR, chave: 'logistica.mensagem_regra', nome: 'Mensagem da regra de status', migrado: true, editavel: true, desligavel: false,
  interruptor: 'fixo', ao_desligar: '—', ao_desligar_texto: '', escopo_tipo: 'regra_status', por_que_nao_edita: null,
  quem_recebe: { via: 'cadastro', motivo: null, fonte: null, nomes: [{ nome: 'harry potter', tipo: 'usuario' }], avisos: [], total: 1 },
  destinatarios: [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter', valido: true, aviso: null }],
  regras: [{ id: 'r1', plataforma: null, status_plataforma: 'Extraviado', tem_mensagem: true, mensagem: 'Confira', usa_padrao: true, antes: { nomes: [], avisos: [] }, cadastro: [] }],
}
const SEM_LOTES = { items: [], mais: false }

;(async () => {
  // ---------------------------------------------------------------- lib
  {
    assert.equal(lib.textoConfirmarDesligar(MAC), 'Desligar "Códigos de devolução do ML"?\nEnquanto estiver desligado, ao religar, os pendentes saem de uma vez.')
    assert.equal(
      lib.textoConfirmarDesligar({ nome: 'X', ao_desligar: 'descarta', ao_desligar_texto: 'os avisos deste período se perdem' }),
      'Desligar "X"?\nEnquanto estiver desligado, os avisos deste período se perdem.',
    )
    assert.equal(lib.textoConfirmarTeste(SERVIDOR), 'Mandar "[TESTE] Informar da Logística" pelo Threema agora para: harry potter, heisenberg?')
    for (const ok of ['ABCD1234', ' abcd1234 ', '*ABC1234']) assert.ok(lib.threemaIdValido(ok), ok)
    for (const ruim of ['', 'ABC', 'ABCD12345', 'ABCD-123', '**ABC123']) assert.ok(!lib.threemaIdValido(ruim), ruim)
    assert.equal(lib.normalizarThreema(' abcd1234 '), 'ABCD1234')
    const r = {
      usuario: { id: 'u', nome: 'cairo sa', tem_threema: true },
      envios: [
        { chave: 'a', nome: 'Prazo de contestação', painel: 'Devoluções', onde_roda: 'servidor', via: 'legado', unico: true, grupo: null },
        { chave: 'b', nome: 'Kit', painel: 'Controle de Estoque', onde_roda: 'servidor', via: 'legado', unico: false, grupo: null },
      ],
      total: 2, unicos: 1, pela_configuracao_antiga: 2,
    }
    const t = lib.textoAvisoPessoa(r, 'suspender esta pessoa')
    assert.match(t, /^cairo sa recebe 2 envios do Threema\./)
    assert.match(t, /Em 1 é a única pessoa — eles param de chegar a alguém: Prazo de contestação\./)
    assert.match(t, /2 usam a configuração antiga: o ID dela continua nessas listas \(suspender não tira\)/)
    assert.match(t, /Quer mesmo suspender esta pessoa\?$/)
    assert.equal(lib.textoAvisoPessoa({ ...r, envios: [], total: 0, unicos: 0, pela_configuracao_antiga: 0 }, 'x'), null)
  }

  // ---------------------------------------------------------------- gaveta: envio do Mac
  {
    let detalhe = MAC
    const m = montar('../components/EnviosThreemaGaveta.vue', {
      props: { chave: MAC.chave, regra: null },
      responder: (url, opts) => {
        if (url === '/api/threema/pessoas') return PESSOAS
        if (url.endsWith('/registros')) return SEM_LOTES
        if (opts.method === 'PATCH') {
          detalhe = { ...MAC, ativo: false, destinatarios: [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter', valido: true, aviso: null }], avisos_da_edicao: ['O Mac ainda usa o .env: esta lista passa a valer quando o Mac ler o Cadastro.'] }
          return detalhe
        }
        return detalhe
      },
    })
    await esperar(); await esperar(); await esperar()
    assert.ok(m.chamadas.some((c) => c.url === '/api/threema/pessoas'), 'o editável carrega as pessoas')
    let html = await m.html()
    assert.match(html, /data-bloco="edicao"/)
    assert.match(html, /O Mac ainda usa o \.env: esta lista passa a valer quando o Mac ler o Cadastro\./)
    assert.match(html, /data-bloco="pessoas"[\s\S]*data-bloco="grupos"[\s\S]*data-bloco="contatos"/, 'os três blocos')
    assert.match(html, /<button[^>]*disabled[^>]*data-pessoa="u-sem"/, 'sem Threema: apagado')
    assert.match(html, /to="\/users\/u-sem"[^>]*>[^<]*Sem Threema sem Threema: acertar/, 'com o link para acertar em Usuários')
    assert.ok(!/Antigo/.test(html), 'contato inativo não aparece para escolher')
    assert.ok(!/data-acao="testar"/.test(html), 'o envio do Mac não tem teste daqui')
    assert.match(html, /data-acao="salvar"[^>]*disabled|disabled[^>]*data-acao="salvar"/, 'sem mudança, Salvar fica apagado')

    // desligar pede confirmação; recusar não grava
    m.estado.form.ativo = false
    m.estado.form.lista = [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter' }]
    responderConfirm = false
    await m.estado.salvar()
    assert.equal(confirmacoes.at(-1), lib.textoConfirmarDesligar(MAC))
    assert.ok(!m.chamadas.some((c) => c.method === 'PATCH'))
    responderConfirm = true
    await m.estado.salvar()
    const patch = m.chamadas.find((c) => c.method === 'PATCH')
    assert.equal(patch.url, '/api/threema/envios/devolucoes.codigos_ml_tuta')
    assert.deepEqual(patch.body, { ativo: false, destinatarios: [{ tipo: 'usuario', id: 'u-h' }] }, 'só o que mudou')
    assert.deepEqual(m.emitidos.at(-1), ['mudou'])
    assert.match(m.estado.msg, /^Salvo\. O Mac ainda usa o \.env/)
    assert.equal(m.estado.temMudanca, false)
    // observação sozinha
    m.estado.form.observacao = ' confere o ID '
    await m.estado.salvar()
    assert.deepEqual(m.chamadas.filter((c) => c.method === 'PATCH').at(-1).body, { observacao: 'confere o ID' })
  }

  // ---------------------------------------------------------------- gaveta: servidor não migrado
  {
    const m = montar('../components/EnviosThreemaGaveta.vue', {
      props: { chave: SERVIDOR.chave, regra: null },
      responder: (url, opts) => {
        if (url.endsWith('/teste')) return { enviados: ['harry potter'], falharam: ['heisenberg'], remetente: 'logistica' }
        if (url.endsWith('/registros')) return SEM_LOTES
        return SERVIDOR
      },
    })
    await esperar(); await esperar()
    assert.ok(!m.chamadas.some((c) => c.url === '/api/threema/pessoas'), 'só leitura não carrega o seletor')
    const html = await m.html()
    assert.ok(!/data-bloco="edicao"/.test(html), 'não migrado: sem edição')
    assert.match(html, /data-so-leitura[^>]*>Ainda usa a configuração antiga\. Para mudar quem recebe: Logística › Informar\./)
    assert.match(html, /data-acao="testar"/)
    await m.estado.testar()
    assert.equal(confirmacoes.at(-1), lib.textoConfirmarTeste(SERVIDOR))
    const post = m.chamadas.find((c) => c.method === 'POST')
    assert.equal(post.url, '/api/threema/envios/logistica.informar/teste')
    assert.equal(m.estado.msg, 'Teste enviado (remetente logistica) para: harry potter. Não chegou para: heisenberg.')
  }

  // ---------------------------------------------------------------- gaveta: item 1 migrado
  {
    const m = montar('../components/EnviosThreemaGaveta.vue', {
      props: { chave: ITEM1.chave, regra: 'r1' },
      responder: (url, opts) => {
        if (url === '/api/threema/pessoas') return PESSOAS
        if (url.endsWith('/registros')) return SEM_LOTES
        return ITEM1
      },
    })
    await esperar(); await esperar(); await esperar()
    let html = await m.html()
    assert.match(html, /data-sem-interruptor[^>]*>\s*Sem interruptor: quem liga e desliga é a Mensagem de cada regra\./)
    assert.ok(!/data-campo="ativo"/.test(html), 'item 1 sem o liga/desliga')
    assert.match(html, /Quem recebe \(padrão\)/)
    assert.match(html, /data-regra="r1"[\s\S]*Lista própria/)
    m.estado.editarRegra(ITEM1.regras[0])
    m.estado.listaRegra = [{ tipo: 'contato', id: 'c-r', nome: 'Roma' }]
    await m.estado.salvarRegra(ITEM1.regras[0])
    const put = m.chamadas.find((c) => c.method === 'PUT')
    assert.equal(put.url, '/api/threema/envios/logistica.mensagem_regra/regras/r1')
    assert.deepEqual(put.body, { destinatarios: [{ tipo: 'contato', id: 'c-r' }] })
    await m.estado.salvarRegra(ITEM1.regras[0], true)
    assert.equal(m.chamadas.at(-1).method, 'DELETE')
  }

  // ---------------------------------------------------------------- a página: abas e emergência
  {
    const LISTA = {
      gerado_em: 'x', emergencia: { usar_legado: false, alterado_por: null, alterado_em: null, env_desligado: false },
      paineis: ['Logística'], contagem: {}, total: 60, envios: [SERVIDOR], pessoa: null,
    }
    const m = montar('../pages/envios-threema.vue', { responder: () => LISTA })
    await m.montar()
    let html = await m.html()
    assert.match(html, /aria-current="page"[^>]*>Envios<\/button>/)
    assert.match(html, />Contatos externos<\/button>/)
    assert.match(html, /data-acao="emergencia"[^>]*>Ligar a emergência</)
    m.estado.trocarAba('grupos')
    assert.equal(m.route.query.aba, 'grupos')
    html = await m.html()
    assert.match(html, /<EnviosThreemaGrupos/i)
    assert.ok(!/data-envio=/.test(html), 'na aba Grupos a lista de envios some')
    m.estado.trocarAba('envios')
    assert.equal(m.route.query.aba, undefined)
    responderConfirm = false
    await m.estado.alternarEmergencia()
    assert.match(confirmacoes.at(-1), /^Ligar a emergência\? TODOS os envios voltam para a configuração antiga/)
    assert.ok(!m.chamadas.some((c) => c.method === 'POST'))
    responderConfirm = true
    await m.estado.alternarEmergencia()
    const post = m.chamadas.find((c) => c.method === 'POST')
    assert.deepEqual(post, { url: '/api/threema/emergencia', method: 'POST', query: {}, body: { usar_legado: true } })
    assert.equal(m.chamadas.at(-1).url, '/api/threema/envios', 'recarrega depois')
  }

  // ---------------------------------------------------------------- seletor
  {
    const m = montar('../components/EnviosThreemaSeletor.vue', {
      props: { modelValue: [{ tipo: 'grupo', id: 'g-1', nome: 'Estoque e NF' }], pessoas: PESSOAS, tipos: ['usuario', 'contato'], max: 2 },
      responder: () => ({}),
    })
    let html = await m.html()
    assert.ok(!/data-bloco="grupos"/.test(html), 'membro de grupo não é grupo')
    assert.match(html, /Estoque e NF <span class="text-muted-foreground">\(grupo\)/)
    m.estado.adicionar({ tipo: 'usuario', id: 'u-h', nome: 'harry potter' })
    assert.deepEqual(m.emitidos.at(-1), ['update:modelValue', [
      { tipo: 'grupo', id: 'g-1', nome: 'Estoque e NF' }, { tipo: 'usuario', id: 'u-h', nome: 'harry potter' },
    ]])
    m.props.modelValue = m.emitidos.at(-1)[1]
    html = await m.html()
    assert.match(html, /No máximo 2 por lista\./)
    m.estado.tirar(0)
    assert.deepEqual(m.emitidos.at(-1)[1], [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter' }])
    m.estado.busca = 'rom'
    html = await m.html()
    assert.match(html, /Roma/)
    assert.ok(!/harry potter<!?-?-?>?\s*<\/button>/.test(html.replace(/data-escolhidos[\s\S]*?<\/div>/, '')))
  }

  // ---------------------------------------------------------------- grupos
  {
    let falhar = false
    const m = montar('../components/EnviosThreemaGrupos.vue', {
      responder: (url, opts) => {
        if (opts.method === 'DELETE' && falhar) {
          const e = new Error('409'); e.data = { detail: { code: 'grupo_em_uso', message: "O grupo 'Estoque e NF' está em uso: Códigos. Tire-o desses envios antes." } }; throw e
        }
        return url === '/api/threema/pessoas' ? PESSOAS : { id: 'g-2', nome: 'Novo', descricao: null }
      },
    })
    await m.montar()
    let html = await m.html()
    assert.match(html, /Estoque e NF/)
    assert.match(html, /usado em 1 envio/)
    m.estado.novo()
    m.estado.form.nome = 'Chamados ML (Mac)'
    m.estado.form.membros = [{ tipo: 'usuario', id: 'u-h', nome: 'harry potter' }]
    await m.estado.salvar()
    const post = m.chamadas.find((c) => c.method === 'POST')
    assert.deepEqual(post.body, { nome: 'Chamados ML (Mac)', descricao: null, membros: [{ tipo: 'usuario', id: 'u-h' }] })
    falhar = true
    await m.estado.apagar(PESSOAS.grupos[0])
    html = await m.html()
    assert.match(html, /data-erro[^>]*>O grupo &#39;Estoque e NF&#39; está em uso: Códigos\./)
    assert.ok(!/Grupo criado\./.test(html), 'o aviso de sucesso anterior some')
  }

  // ---------------------------------------------------------------- contatos
  {
    const m = montar('../components/EnviosThreemaContatos.vue', {
      responder: (url, opts) => {
        if (opts.method === 'POST') {
          const e = new Error('409'); e.data = { detail: { code: 'id_de_usuario', message: 'Esse ID já é de harry potter (Configurações › Usuários): escolha a pessoa em Pessoas.' } }; throw e
        }
        return PESSOAS
      },
    })
    await m.montar()
    let html = await m.html()
    assert.match(html, /Roma[\s\S]*veio da Ouvidoria[\s\S]*VB…/)
    m.estado.novo.nome = 'Lisboa'
    m.estado.novo.threema_id = 'abc'
    html = await m.html()
    assert.match(html, /data-id-invalido/)
    await m.estado.criar()
    assert.ok(!m.chamadas.some((c) => c.method === 'POST'), 'ID fora do formato não vai')
    m.estado.novo.threema_id = 'tsth0002'
    await m.estado.criar()
    assert.equal(m.chamadas.at(-1).method, 'POST')
    html = await m.html()
    assert.match(html, /data-erro[^>]*>Esse ID já é de harry potter/)
  }

  // ---------------------------------------------------------------- Usuários
  {
    const lista = fs.readFileSync(path.resolve(__dirname, '../pages/users/index.vue'), 'utf8')
    assert.match(lista, /if \(v && !threemaIdValido\(v\)\) \{/)
    assert.match(lista, /api<EnviosDaPessoa>\(`\/api\/threema\/pessoas\/\$\{u\.id\}\/envios`\)/)
    assert.match(lista, /confirmarEnviosThreema\(u, v \? 'trocar o Threema' : 'apagar o Threema'\)/)
    const um = fs.readFileSync(path.resolve(__dirname, '../pages/users/[id].vue'), 'utf8')
    assert.match(um, /threemaNovo && !threemaIdValido\(threemaNovo\)/)
    assert.match(um, /'suspender esta pessoa'/)
    assert.match(um, /textoEnviosThreema\('excluir \(desabilitar\) este usuário'\)/)
    sfc('../pages/users/index.vue')
    sfc('../pages/users/[id].vue')
  }

  console.log('envios-threema-edicao-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
