// node tests/atendimento-publicacao.cjs — o painel da PUBLICAÇÃO das redes (RF7, frente B, 02/10/2026).
// Comentários e menções do Instagram e da Página do Facebook (Charlots e
// Uranyx) viram conversa `comentario`; o cartão do topo mostra a
// publicação e os comentários, e responde/oculta. Aqui, a tela:
//  - o contrato com o backend (rota GET /publicacao, as três ações, os campos
//    do PublicacaoPainelOut/ComentarioOut de routers/atendimento_redes.py);
//  - os ajudantes puros: o fio desta conversa em destaque, o comentário que a
//    caixa responde por padrão, a caixa desabilitada com o porquê (envio
//    desligado, observar, sem permissão), as confirmações ("Responder em
//    PÚBLICO?"), só https em <img>/link;
//  - o template: as duas abas (público × Direct), o aviso, o botão
//    desabilitado com o motivo e o "Abrir na rede" em outra aba.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const rel = '../components/AtendimentoPublicacao.vue'
const filename = path.resolve(__dirname, rel)
const fonte = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(fonte, { filename })
assert.deepEqual(errors, [], rel)
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'pub', compilerOptions: { isTS: true } })
assert.deepEqual(compiled.errors, [], 'o template compila')
compileScript(descriptor, { id: 'pub' })
const mod = { exports: {} }
new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
const P = mod.exports
const setup = descriptor.scriptSetup.content
const template = descriptor.template.content
const router = fs.readFileSync(path.resolve(__dirname, '../../api/app/routers/atendimento_redes.py'), 'utf8')
const servico = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/atendimento/redes.py'), 'utf8')

// ------------------------------------------------ o contrato com o backend
assert.ok(setup.includes('/api/atendimento/conversas/${encodeURIComponent(id)}/publicacao'), 'GET /publicacao')
assert.ok(setup.includes("'responder-direct' : 'responder'"), 'as duas rotas de resposta')
assert.ok(setup.includes('/ocultar`'), 'a rota de ocultar')
assert.ok(setup.includes('confirmar: true'), 'a ação manda a confirmação que o backend exige')
for (const rota of ['"/conversas/{conversa_id}/publicacao"', '"/comentarios/{comentario_id}/responder"', '"/comentarios/{comentario_id}/responder-direct"', '"/comentarios/{comentario_id}/ocultar"']) {
  assert.ok(router.includes(rota), `o router tem ${rota}`)
}
// Os campos que a tela lê existem no schema do backend.
const campos = (classe) => {
  const bloco = (router.match(new RegExp(`class ${classe}\\(BaseModel\\):([\\s\\S]*?)\\n\\n\\n`)) || [])[1] || ''
  return [...bloco.matchAll(/^ {4}([a-z_]+):/gm)].map((m) => m[1])
}
for (const campo of ['id', 'externo_id', 'pai_externo_id', 'autor_username', 'da_marca', 'texto', 'criado_em', 'oculto', 'eh_pergunta', 'mencao', 'desta_conversa', 'respondido', 'resposta_privada_em', 'pode_responder', 'motivo_responder', 'pode_direct', 'motivo_direct', 'pode_ocultar', 'motivo_ocultar', 'respostas']) {
  assert.ok(campos('ComentarioOut').includes(campo), `ComentarioOut.${campo}`)
}
for (const campo of ['origem', 'formato', 'tipo', 'legenda', 'link', 'miniatura_url', 'miniatura_vencida', 'publicada_em', 'curtidas', 'comentarios', 'conta_nome', 'autor_username']) {
  assert.ok(campos('PublicacaoOut').includes(campo), `PublicacaoOut.${campo}`)
}
for (const campo of ['publicacao', 'comentarios', 'total_comentarios', 'truncado', 'interacoes_anteriores', 'canal_status', 'canal_erro', 'envio']) {
  assert.ok(campos('PublicacaoPainelOut').includes(campo), `PublicacaoPainelOut.${campo}`)
}
for (const campo of ['ativo', 'motivo', 'aviso_publico', 'aviso_direct', 'limite_publico', 'limite_direct']) {
  assert.ok(campos('EnvioRedeOut').includes(campo), `EnvioRedeOut.${campo}`)
}
// Os códigos de recusa que a tela mostra com a frase do backend existem lá.
const codigos = [...setup.matchAll(/'([a-z_]+)'/g)].map((m) => m[1])
for (const code of ['confirmar_publico', 'comentario_oculto', 'sem_resposta_privada', 'prazo_resposta_privada', 'resposta_privada_ja_enviada', 'sem_ocultar', 'rede_recusou']) {
  assert.ok(codigos.includes(code), `a tela conhece ${code}`)
  assert.ok(servico.includes(`"${code}"`), `o serviço usa ${code}`)
}
// Os rótulos de formato são os mesmos do backend.
for (const [k, v] of Object.entries(P.ROTULO_FORMATO)) {
  assert.ok(servico.includes(`"${k}": "${v}"`), `formato ${k} igual ao redes.ROTULO_FORMATO`)
}

// ------------------------------------------------ os ajudantes
const c = (o = {}) => ({
  id: 'x', externo_id: 'x', pai_externo_id: null, autor_username: 'ana', da_marca: false, texto: 'oi',
  criado_em: '2026-10-02T10:00:00Z', curtidas: null, oculto: false, eh_pergunta: false, mencao: false,
  desta_conversa: false, respondido: false, resposta_privada_em: null, pode_responder: true,
  motivo_responder: null, pode_direct: true, motivo_direct: null, pode_ocultar: true, motivo_ocultar: null,
  respostas: [], ...o,
})
assert.equal(P.formatoDe('REELS'), 'Reels')
assert.equal(P.formatoDe('carousel_album'), 'Carrossel')
assert.equal(P.formatoDe(null), 'Post')
assert.ok(P.ehVideo('REELS') && P.ehVideo('VIDEO') && !P.ehVideo('IMAGE'))
assert.equal(P.nomeDaRede('facebook'), 'Facebook')
assert.equal(P.nomeDaRede('instagram'), 'Instagram')
assert.equal(P.autorDe(c(), 'instagram'), '@ana')
assert.equal(P.autorDe(c({ autor_username: 'Caio Souza' }), 'facebook'), 'Caio Souza')
assert.equal(P.autorDe(c({ da_marca: true }), 'instagram'), 'marca')
assert.equal(P.urlHttps('https://cdn.x/a.jpg'), 'https://cdn.x/a.jpg')
for (const ruim of ['http://x/a.jpg', 'javascript:alert(1)', 'data:image/png;base64,AA', '', null, 'https://x/"onerror=']) {
  assert.equal(P.urlHttps(ruim), null, String(ruim))
}

// O fio desta conversa primeiro (o de topo dela, ou o que tem resposta dela).
const fioDela = c({ id: 'a', desta_conversa: true })
const fioComRespostaDela = c({ id: 'b', respostas: [c({ id: 'b1', desta_conversa: true })] })
const outro = c({ id: 'z', respostas: [c({ id: 'z1', da_marca: true })] })
const g = P.separarComentarios([outro, fioDela, fioComRespostaDela])
assert.deepEqual(g.desta.map((x) => x.id), ['a', 'b'])
assert.deepEqual(g.outros.map((x) => x.id), ['z'])
assert.deepEqual(P.separarComentarios(null), { desta: [], outros: [] })
assert.deepEqual(P.todosOsComentarios([outro]).map((x) => x.id), ['z', 'z1'])

// O alvo padrão: o mais recente desta conversa SEM resposta; senão o mais recente dela.
const velho = c({ id: 'v', desta_conversa: true, criado_em: '2026-10-01T10:00:00Z' })
const novoRespondido = c({ id: 'n', desta_conversa: true, respondido: true, criado_em: '2026-10-02T10:00:00Z' })
assert.equal(P.alvoPadrao([novoRespondido, velho]).id, 'v')
assert.equal(P.alvoPadrao([novoRespondido]).id, 'n')
assert.equal(P.alvoPadrao([c({ id: 'm', desta_conversa: true, da_marca: true })]), null)
assert.equal(P.alvoPadrao([]), null)

// A caixa: desabilitada com o porquê do backend; sem permissão de editar; sem alvo.
const desligado = 'O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO)'
assert.deepEqual(P.caixaDe(c({ pode_responder: false, motivo_responder: desligado }), 'publico', true), { modo: 'desligada', motivo: desligado })
assert.deepEqual(P.caixaDe(c({ pode_direct: false, motivo_direct: 'prazo' }), 'direct', true), { modo: 'desligada', motivo: 'prazo' })
assert.equal(P.caixaDe(c(), 'publico', false).modo, 'desligada')
// Fase de observação (07/10/2026): sem edição = quem só lê.
assert.match(P.caixaDe(c(), 'publico', false).motivo, /^Só leitura por enquanto/)
assert.equal(P.caixaDe(null, 'publico', true).modo, 'desligada')
assert.deepEqual(P.caixaDe(c(), 'direct', true), { modo: 'caixa', motivo: '' })

// As confirmações.
assert.match(P.perguntaPublica(null, 'Temos!'), /^Responder em PÚBLICO\?/)
assert.match(P.perguntaPublica('aviso X', 'Temos!'), /aviso X[\s\S]*Temos!/)
assert.match(P.perguntaDirect('uma só', '@ana', 'oi'), /^Responder no Direct de @ana\?[\s\S]*uma só/)
assert.match(P.perguntaOcultar({ oculto: false }, 'Instagram'), /^Ocultar o comentário no Instagram\?/)
assert.match(P.perguntaOcultar({ oculto: true }, 'Facebook'), /^Mostrar o comentário de novo no Facebook\?/)
assert.equal(P.seloDe(c({ da_marca: true })), null)
assert.equal(P.seloDe(c({ oculto: true })).rotulo, 'oculto')
assert.equal(P.seloDe(c({ respondido: true })).rotulo, 'respondido')
assert.equal(P.seloDe(c()).rotulo, 'sem resposta')

// ------------------------------------------------ o template
assert.ok(template.includes('Comentário (público)') && template.includes('Direct (privado)'), 'as duas abas')
assert.ok(template.includes('Responder em público') && template.includes('Responder no Direct'), 'os dois botões')
assert.ok(/:disabled="!podeEnviar"/.test(template), 'o botão respeita a caixa')
assert.ok(template.includes('data-motivo-sem-resposta'), 'o porquê aparece quando a caixa está desligada')
assert.ok(/target="_blank"\s+rel="noopener noreferrer"/.test(template), '"Abrir na rede" em outra aba, sem opener')
assert.ok(template.includes('referrerpolicy="no-referrer"'), 'a miniatura sem referrer')
assert.ok(template.includes('data-canal-erro'), 'o sem_escopo da conta aparece no painel')
assert.ok(setup.includes('confirm(pergunta)') && setup.includes('confirm(perguntaOcultar('), 'confirma antes de agir na rede')

// ------------------------------------------------ revisão 02/10: menção e "Marcar como resolvido"
// A menção nasce "não precisa de resposta" (a resposta da marca no post de
// outra pessoa nunca volta): o cartão explica, e o "Marcar como resolvido"
// (RF7) fecha a conversa pelo PATCH dela (evento `resolver`), com confirmação.
assert.ok(template.includes('data-aviso-mencao') && /pub\.tipo === 'mencao'/.test(template), 'o aviso da menção')
assert.match(template, /A Meta não deixa o DaVinci ler os comentários do post de outra pessoa/)
assert.ok(template.includes('data-resolver') && template.includes('Marcar como resolvido'), 'o botão Marcar como resolvido')
assert.match(template, /v-if="canEdit && conversa\.situacao !== 'fechada'"[\s\S]{0,400}@click="resolver"/, 'só com edição e com a conversa aberta')
assert.ok(setup.includes("confirm(perguntaResolver(pub.value?.tipo === 'mencao'))") && setup.includes("emit('resolver')"), 'confirma e pede à conversa')
assert.match(P.perguntaResolver(true), /^Marcar como resolvido\?[\s\S]*Uma menção nova vira outra conversa/)
assert.match(P.perguntaResolver(false), /Nada é publicado na rede/)
const conversaSfc = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
assert.match(conversaSfc, /@resolver="patch\(\{ situacao: 'fechada' \}, 'fechar', 'Marcada como resolvida'\)"/, 'a conversa fecha pelo PATCH (a lista acompanha)')
assert.match(conversaSfc, /v-else-if="conversa\.plataforma === 'amazon' \|\| canalExterno === 'comentario'"/, '"Não precisa de resposta" também no comentário')
assert.match(servico, /if criada and not linha\.eh_pergunta:\s+#[\s\S]{0,400}conversa\.sem_resposta_necessaria = True/, 'a menção nasce sem a vez da loja')
// O selo "pergunta" na lista (a frente da fila no Mídia e no Falta responder).
const lista = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoLista.vue'), 'utf8')
assert.ok(lista.includes('v-if="c.eh_pergunta"') && lista.includes('data-selo-pergunta'), 'o selo pergunta na lista')
assert.match(lista, /function temSelos[\s\S]{0,120}c\.eh_pergunta/)
const schemas = fs.readFileSync(path.resolve(__dirname, '../../api/app/schemas/atendimento.py'), 'utf8')
assert.match(schemas, /class ConversaResumoOut\(BaseModel\):[\s\S]*?eh_pergunta: bool = False/, 'o campo existe na linha da lista')
// Rede social em Humano (responder/ocultar EM PÚBLICO como a marca): só admin.
const canais = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoCanais.vue'), 'utf8')
assert.match(canais, /:disabled="soAdmin\(c, m\.value\)"/)
assert.match(canais, /modo === 'auto' \|\| \(!!c\.externo_ref && REDES\.includes\(c\.plataforma\) && modo === 'humano'\)/)

console.log('ok — atendimento-publicacao')
