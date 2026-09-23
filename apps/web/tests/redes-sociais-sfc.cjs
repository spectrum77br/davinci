// Run from apps/web: node tests/redes-sociais-sfc.cjs
// Cadastros › Redes Sociais (Eduardo, 15/09/2026, v3 — layout da planilha):
// parse + compileTemplate da página (estilo companies-new-account.cjs),
// checagens dos helpers puros e, no molde de marcas-sfc.cjs, execução do
// <script setup> com api/timers falsos pra travar: as colunas da MARCA saem
// por PATCH /api/redes-sociais/marca/{id} (nunca /api/marcas); a senha da
// marca vem de GET .../marca/{id}/sac-senha só no clique, some em 30 s e ao
// recarregar/filtrar/desmontar; o corpo do POST/PATCH da conta nunca leva
// senha vazia nem reenvia a revelada; "voltar a herdar" e "limpar" mandam
// null explícito; tooltip do chip nunca inclui senha. Só dados FALSOS aqui.
//
// v3.2 (Eduardo, 15/09/2026) — Publicação automática: o TOKEN de publicação
// só existe no v-model do sub-modal "conectar", vai no CORPO do POST
// /{id}/conectar e some ao fechar/recarregar/desmontar (nunca em log,
// :title, URL ou localStorage); ok=false devolve as contas que o token
// enxerga pro operador escolher e reenviar com external_user_id; o PATCH da
// conta leva postagem_auto e os tetos, com null explícito quando o campo
// está vazio (= herda o padrão do servidor).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/redes-sociais.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'redes-sociais-check' })
assert.deepEqual(compiled.errors, [])
// O render compilado precisa ser JS válido (imports do vue resolvem via require).
new Function('exports', 'require', transpile(compiled.code))({}, require)

const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- libs reais
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
const redes = loadLib('../lib/redesSociais.ts')
const apiError = loadLib('../lib/apiError.ts')

// ---------------------------------------------------------------- higiene estática
assert.match(script, /permission: \{ resource: 'redes_sociais', action: 'view' \}/, 'permission middleware')
assert.ok(!/type="password"/.test(tpl), 'senha nunca é <input type="password"> (gerenciador de senhas)')
assert.match(tpl, /data-1p-ignore/, 'campo senha ignora 1Password')
assert.match(tpl, /WebkitTextSecurity/, 'campo senha mascarado por CSS')
assert.match(script, /onUnmounted\(/, 'zera senha ao sair da página')
assert.match(script, /30_000/, 'auto-oculta em 30 s')
assert.match(script, /body: \{ senha: null \}/, '"voltar a herdar" manda senha:null explícito')
assert.match(script, /body: \{ sac_senha: null \}/, '"limpar senha" da marca manda sac_senha:null explícito')
// senha_enc é coluna do banco — o front nunca lê/manda esse campo (só em
// comentário explicando que ele não vem).
assert.ok(!/senha_enc/.test(tpl) && !/[.'"]senha_enc\b|senha_enc\s*:/.test(script), 'front nunca usa senha_enc')
// v3: `verificado` (bool) não existe mais — é verificacao_status.
assert.ok(!/\.verificado\b/.test(script + tpl) && !/verificado:\s*(true|false)/.test(script), 'sem o bool `verificado` (v2)')
assert.match(script, /verificacao_status/, 'usa verificacao_status')
// Colunas da marca: só via /api/redes-sociais/marca/{id} (redes_sociais:edit).
// /api/marcas/ só aparece pra miniatura do logo (GET imagem).
assert.ok(!/api\/marcas\/\$\{[^`]*`,\s*\{\s*method/.test(script), 'nenhum PATCH/POST/DELETE em /api/marcas')
assert.match(script, /\/api\/redes-sociais\/marca\/\$\{[^}]+\}`,\s*\{\s*method: 'PATCH'/, 'PATCH /api/redes-sociais/marca/{id}')
assert.match(script, /\/api\/redes-sociais\/marca\/\$\{[^}]+\}\/sac-senha/, 'GET .../marca/{id}/sac-senha')
assert.match(script, /api\/marcas\/\$\{[^}]+\}\/logo\?v=/, 'miniatura do logo com ?v=updated_at')
// A senha nunca vai parar num title/tooltip.
// (referência ao VALOR: .get(), `${…}` ou ramo de ternário — `!form.senha ?` como condição é ok.)
assert.ok(
  !/:title="[^"]*(revealedSenhas\.get\(|\$\{\s*(form\.senha|senhaMarcaValor|tokenValor)\b|[?:]\s*(form\.senha|senhaMarcaValor|tokenValor)\s*(?=[):"]|$))/.test(tpl),
  'senha/token nunca em title',
)

// ---------------------------------------------------------------- token de publicação
// Campo do token com a mesma blindagem da senha (nf-cadastros.vue).
assert.match(tpl, /name="rede-social-token"/, 'sub-modal tem o campo do token')
{
  const campo = tpl.slice(tpl.indexOf('name="rede-social-token"') - 400, tpl.indexOf('name="rede-social-token"') + 400)
  assert.match(campo, /data-1p-ignore/, 'token ignora 1Password')
  assert.match(campo, /WebkitTextSecurity/, 'token mascarado por CSS')
  assert.match(campo, /autocomplete="off"/, 'token sem autocomplete')
  assert.ok(!/type="password"/.test(campo), 'token não é input[type=password]')
}
// O plaintext do token só sai no corpo do POST /conectar — nunca na URL,
// em log ou em armazenamento do navegador.
assert.match(script, /\/conectar`,\s*\{\s*\n?\s*method: 'POST'/, 'POST /{id}/conectar')
assert.match(script, /body: \{ access_token: token, external_user_id: conexaoEscolha\.value \|\| null \}/, 'token vai no corpo, com a escolha do operador')
assert.match(script, /\/conectar`, \{ method: 'DELETE' \}/, 'DELETE /{id}/conectar')
// (as linhas de comentário explicam JUSTAMENTE que não se guarda nada — só
//  o código conta aqui)
const scriptCode = script.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n')
assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(scriptCode + tpl), 'token/senha nunca em armazenamento do navegador')
assert.ok(!/(console\.\w+|api)\([^)]*\btokenValor\b/.test(script), 'tokenValor nunca vai pra log (só pro corpo do POST, via `token`)')
assert.ok(!/\$\{\s*(token|tokenValor)/.test(script), 'token nunca interpolado em URL/template string')
// Zerado ao fechar / recarregar / sair.
assert.match(script, /function fecharConexao\(\)[\s\S]*?tokenValor\.value = ''/, 'fecharConexao zera o token')
assert.match(script, /onUnmounted\(\(\) => \{[\s\S]*?tokenValor\.value = ''/, 'onUnmounted zera o token')
// Indicadorzinho do grid + bloco novo do modal.
assert.match(tpl, /v-if="postagemAutoOn\(r\)"[\s\S]{0,200}title="postagem automática ligada"/, 'pontinho de postagem automática no grid')
assert.match(tpl, /Publicação automática/, 'bloco Publicação automática no modal')
assert.match(tpl, /postar automaticamente nesta conta/, 'checkbox do interruptor')
assert.match(tpl, /publicar manualmente na aba Criativos funciona de qualquer jeito/, 'hint do interruptor')
assert.match(tpl, /placeholder="padrão: 2"/, 'placeholder de posts por dia')
assert.match(tpl, /placeholder="padrão: 90"/, 'placeholder do intervalo mínimo')
assert.match(tpl, /Generate new token/, 'diz de onde vem o token')
assert.match(tpl, /fica cifrado no servidor/, 'aviso de que o token não sai mais dali')
// Só-view: bloco olho/copiar do modal atrás de canEdit; Salvar idem.
assert.match(tpl, /<div v-if="canEdit" class="absolute right-2/, 'olho/copiar do modal só com edit')
assert.match(tpl, /<Button v-if="canEdit" :disabled="saving \|\| !form\.marca_id" @click="saveRede">/, 'Salvar só com edit')
// Guia "Como verificar" com links seguros.
assert.match(tpl, /v-for="g in VERIFICACAO_GUIA"/, 'modal renderiza VERIFICACAO_GUIA')
assert.match(tpl, /target="_blank"\s+rel="noopener"/, 'links do guia com rel=noopener')
// Pontinho do Zap abre o select sem disparar a edição do fone (@click.stop).
assert.match(tpl, /@click\.stop="startEdit\(row, 'whatsapp_verificacao_status'\)"/, 'pontinho do Zap com @click.stop')
// Ordem das colunas = planilha: Marca | Fone/WhatsApp | Usuário | Senha | plataformas | Função/Tipo/Obs.
{
  const thead = tpl.slice(tpl.indexOf('<thead'), tpl.indexOf('</thead>'))
  const pos = ['>Marca<', 'Fone / WhatsApp', 'Usuário', '>Senha<', 'v-for="p in PLATAFORMAS"', 'v-for="col in MARCA_TEXT_COLS"']
    .map((s) => thead.indexOf(s))
  assert.ok(pos.every((p) => p >= 0), `cabeçalhos presentes: ${pos}`)
  assert.deepEqual([...pos].sort((a, b) => a - b), pos, 'ordem das colunas da planilha')
  assert.ok(!/Ações/.test(thead), 'sem coluna Ações')
  assert.match(thead, /sticky left-0/, 'Marca sticky')
}

// Conta que o token enxerga mas não dá pra escolher precisa DIZER por quê —
// senão o operador vê um rádio apagado e não tem como adivinhar o que falta
// (caso real: Página sem Instagram vinculado).
assert.match(script, /function motivoIndisponivel/, 'existe o motivo do rádio desabilitado')
assert.match(tpl, /motivoIndisponivel\(conexao\.rede\.plataforma\)/, 'o motivo aparece no template')
assert.match(script, /sem Instagram vinculado a essa P/, 'texto do motivo para Instagram')

// ---------------------------------------------------------------- helpers puros
const start = script.indexOf('// ---------- helpers puros')
const end = script.indexOf('// ---------- fim helpers puros')
assert.ok(start > 0 && end > start, 'marcadores dos helpers puros presentes')
const helpersJs = transpile(script.slice(start, end), ts.ModuleKind.ESNext)
const H = new Function('PLATAFORMA_LABELS', 'VERIFICACAO_LABELS', 'fmtFone', helpersJs + `
return { montaBody, chipTitle, rowMatches, sincronizaEfetivos, dotClass, VERIFICACAO_CURTO, verifLabel, senhaHintTexto,
  tetoOuNull, postagemAutoOn, tokenPillTexto, tokenPillClass, contaExternaId, contaExternaLabel, TOKEN_STATUS_LABELS };
`)(redes.PLATAFORMA_LABELS, redes.VERIFICACAO_LABELS, redes.fmtFone)

const SENHA_FALSA = 'senha-falsa-teste-123'
// Token FALSO (>= 20 chars, como o validador do backend exige) — nada real.
const TOKEN_FALSO = 'TOKEN-FALSO-DE-TESTE-0123456789'
function form(over = {}) {
  return {
    marca_id: 'm1', plataforma: 'instagram', conta: '  loja.teste ', usuario: '', url: '',
    email: ' SAC@teste.com ', fone: '', senha: '', verificacao_status: 'em_andamento',
    verificacao_obs: '  protocolo 123 ', ativo: false, obs: '  ',
    postagem_auto: false, postagem_max_dia: '', postagem_intervalo_min: '',
    ...over,
  }
}

// montaBody — create: sem senha e sem ativo; textos com trim e vazio → null
{
  const b = H.montaBody(form(), 'create', null)
  assert.ok(!('senha' in b), 'create sem senha digitada não manda a chave')
  assert.ok(!('ativo' in b), 'create não manda ativo (nasce ativa)')
  // RedeSocialCreate não tem os campos de postagem — conta nova nasce sem
  // token e com postagem_auto = false.
  assert.ok(!('postagem_auto' in b) && !('postagem_max_dia' in b) && !('postagem_intervalo_min' in b), 'create não manda postagem_*')
  assert.equal(b.conta, 'loja.teste')
  assert.equal(b.email, 'SAC@teste.com')
  assert.equal(b.usuario, null)
  assert.equal(b.obs, null)
  assert.equal(b.verificacao_status, 'em_andamento')
  assert.equal(b.verificacao_obs, 'protocolo 123')
  assert.equal(b.plataforma, 'instagram')
  assert.ok(!('verificado' in b))
  assert.equal(H.montaBody(form({ verificacao_obs: ' ' }), 'create', null).verificacao_obs, null)
}
// create com senha digitada → vai como está (sem trim)
{
  const b = H.montaBody(form({ senha: SENHA_FALSA + ' ' }), 'create', null)
  assert.equal(b.senha, SENHA_FALSA + ' ')
}
// edit: ativo vai; senha revelada e não alterada NÃO é reenviada
{
  const b = H.montaBody(form({ senha: SENHA_FALSA }), 'edit', SENHA_FALSA)
  assert.equal(b.ativo, false)
  assert.ok(!('senha' in b), 'senha revelada igual à salva não é reenviada')
  // Postagem: interruptor sempre vai; teto vazio = null EXPLÍCITO (volta a
  // herdar o padrão do servidor).
  assert.equal(b.postagem_auto, false)
  assert.equal(b.postagem_max_dia, null)
  assert.equal(b.postagem_intervalo_min, null)
  const b2 = H.montaBody(form({ postagem_auto: true, postagem_max_dia: 4, postagem_intervalo_min: ' 30 ' }), 'edit', null)
  assert.equal(b2.postagem_auto, true)
  assert.equal(b2.postagem_max_dia, 4)
  assert.equal(b2.postagem_intervalo_min, 30)
  // Perfil do AdsPower: vai com trim, e vazio vira null EXPLÍCITO — é assim
  // que dá pra DESLIGAR o executor numa conta sem apagar a conta.
  assert.equal(H.montaBody(form({ adspower_user_id: '  k1dohvrh ' }), 'edit', null).adspower_user_id, 'k1dohvrh')
  assert.equal(H.montaBody(form({ adspower_user_id: '   ' }), 'edit', null).adspower_user_id, null)
  // Na criação o bloco de publicação nem aparece: não pode vazar campo.
  assert.ok(!('adspower_user_id' in H.montaBody(form({ adspower_user_id: 'x' }), 'create', null)))
  // O token NUNCA passa pelo corpo da conta (vai só no /conectar).
  assert.ok(!JSON.stringify(b2).includes(TOKEN_FALSO) && !('access_token' in b2))
}

// tetoOuNull: vazio/lixo = null (herda o padrão), número positivo inteiro passa
{
  assert.equal(H.tetoOuNull(''), null)
  assert.equal(H.tetoOuNull('   '), null)
  assert.equal(H.tetoOuNull(null), null)
  assert.equal(H.tetoOuNull(undefined), null)
  assert.equal(H.tetoOuNull('0'), null, 'zero não é teto')
  assert.equal(H.tetoOuNull('-3'), null)
  assert.equal(H.tetoOuNull('abc'), null)
  assert.equal(H.tetoOuNull(' 5 '), 5)
  assert.equal(H.tetoOuNull(3), 3)
  assert.equal(H.tetoOuNull('2.7'), 2, 'fração vira inteiro')
}

// postagemAutoOn: o robô precisa dos DOIS (token + interruptor)
{
  assert.equal(H.postagemAutoOn({ postagem_auto: true, has_token: true }), true)
  assert.equal(H.postagemAutoOn({ postagem_auto: true, has_token: false }), false)
  assert.equal(H.postagemAutoOn({ postagem_auto: false, has_token: true }), false)
  assert.equal(H.postagemAutoOn({}), false)
}

// tokenPillTexto/Class: estado da credencial — nunca o token
{
  assert.equal(H.tokenPillTexto(null), 'sem token')
  assert.equal(H.tokenPillTexto({ has_token: false, token_conta_externa: 'poofy' }), 'sem token')
  assert.equal(H.tokenPillTexto({ has_token: true, token_conta_externa: '@poofy', token_status: 'ok' }), 'conectado como @poofy')
  assert.equal(H.tokenPillTexto({ has_token: true, token_conta_externa: null }), 'conectado')
  assert.equal(H.tokenPillTexto({ has_token: true, token_conta_externa: 'poofy', token_status: 'expirado' }), 'conectado como @poofy · token vencido')
  assert.equal(H.tokenPillTexto({ has_token: true, token_conta_externa: 'poofy', token_status: 'revogado' }), 'conectado como @poofy · token revogado')
  assert.ok(!H.tokenPillTexto({ has_token: true, token_conta_externa: TOKEN_FALSO }).includes('access_token'))
  assert.match(H.tokenPillClass(null), /bg-muted/)
  assert.match(H.tokenPillClass({ has_token: true }), /emerald/)
  assert.match(H.tokenPillClass({ has_token: true, token_status: 'expirado' }), /amber/)
  assert.deepEqual(Object.keys(H.TOKEN_STATUS_LABELS).sort(), ['expirado', 'ok', 'revogado'])
}

// contaExternaId/Label: o rádio manda o id que o backend espera
{
  const c = { page_id: 'p1', page_nome: 'Poofy Oficial', ig_user_id: 'ig1', ig_username: 'poofy.oficial' }
  assert.equal(H.contaExternaId(c, 'instagram'), 'ig1', 'Instagram casa por ig_user_id')
  assert.equal(H.contaExternaId(c, 'facebook'), 'p1', 'Página casa por page_id')
  assert.equal(H.contaExternaId({ page_id: null, ig_user_id: null }, 'instagram'), '', 'sem id = opção desabilitada')
  assert.equal(H.contaExternaLabel(c), 'Poofy Oficial · @poofy.oficial')
  assert.equal(H.contaExternaLabel({ page_nome: null, ig_username: '@só.ig' }), '@só.ig', 'não duplica o @')
  assert.equal(H.contaExternaLabel({ page_nome: 'Só Página' }), 'Só Página')
  assert.equal(H.contaExternaLabel({ page_id: 'p9' }), 'p9')
  assert.equal(H.contaExternaLabel({}), 'conta sem nome')
}
// edit: senha alterada por cima da revelada → vai
{
  const b = H.montaBody(form({ senha: SENHA_FALSA + 'x' }), 'edit', SENHA_FALSA)
  assert.equal(b.senha, SENHA_FALSA + 'x')
}
// edit: campo vazio → chave ausente (backend mantém); nunca senha:null aqui
{
  const b = H.montaBody(form({ senha: '' }), 'edit', null)
  assert.ok(!('senha' in b))
  assert.ok(!Object.values(b).includes(SENHA_FALSA))
}

// chipTitle: contato EFETIVO ("da marca" quando herdado), verificação, origem da senha — nunca a senha
function redeOut(over = {}) {
  return {
    id: 'r1', marca_id: 'm1', marca_nome: 'Loja Teste', plataforma: 'tiktok', conta: 'loja.teste',
    usuario: 'login.teste', url: null, email: null, fone: null, has_senha: false,
    email_efetivo: 'sac@teste.com', fone_efetivo: '11999990000', has_senha_efetiva: true, senha_origem: 'marca',
    verificacao_status: 'em_andamento', verificacao_obs: 'protocolo 9', obs: 'obs fake', ativo: false,
    postagem_auto: false, postagem_max_dia: null, postagem_intervalo_min: null,
    has_token: false, token_status: null, token_conta_externa: null, token_expires_at: null,
    created_at: '', updated_at: '',
    ...over,
  }
}
{
  const t = H.chipTitle(redeOut())
  assert.match(t, /^TikTok · /)
  assert.match(t, /login: login\.teste/)
  assert.match(t, /sac@teste\.com \(da marca\)/, 'e-mail herdado marcado')
  assert.match(t, /fone: \(11\) 99999-0000 \(da marca\)/, 'fone herdado formatado e marcado')
  assert.match(t, /em andamento/)
  assert.match(t, /protocolo 9/)
  assert.match(t, /inativa/)
  assert.match(t, /senha da marca/)
  assert.match(t, /obs fake/)
  assert.ok(!t.includes(SENHA_FALSA))
  // próprios da conta: sem "(da marca)"; senha própria; verificado
  const t2 = H.chipTitle(redeOut({ email: 'ig@teste.com', email_efetivo: 'ig@teste.com', fone: '11', fone_efetivo: '11', has_senha: true, senha_origem: 'conta', verificacao_status: 'verificado', verificacao_obs: null }))
  assert.match(t2, / · ig@teste\.com · /)
  assert.ok(!t2.includes('(da marca)'))
  assert.match(t2, /senha própria/)
  assert.match(t2, /verificado/)
  // não solicitado não aparece; sem senha nenhuma não fala de senha; sem
  // postagem automática não fala de postagem
  const t3 = H.chipTitle(redeOut({ verificacao_status: 'nao_solicitado', verificacao_obs: null, senha_origem: null, has_senha_efetiva: false }))
  assert.ok(!t3.includes('não solicitado') && !t3.includes('senha') && !t3.includes('postagem'))
  // postagem automática: o tooltip diz que está ligada — e avisa quando falta token
  const t4 = H.chipTitle(redeOut({ postagem_auto: true, has_token: true }))
  assert.match(t4, /· postagem automática ·/)
  assert.ok(!t4.includes('sem token'))
  assert.match(H.chipTitle(redeOut({ postagem_auto: true, has_token: false })), /postagem automática \(sem token\)/)
  // o tooltip nunca carrega o token (a API nem devolve o valor)
  assert.ok(!H.chipTitle(redeOut({ postagem_auto: true, has_token: true, token_conta_externa: 'poofy' })).includes(TOKEN_FALSO))
  // plataforma fora do lib (removida do enum) cai no valor cru, sem quebrar
  assert.match(H.chipTitle(redeOut({ plataforma: 'orkut', usuario: null, email_efetivo: null, fone_efetivo: null, verificacao_status: 'nao_solicitado', verificacao_obs: null, ativo: true, senha_origem: null, obs: null })), /^orkut$/)
}

// rowMatches: marca (nome/slug/e-mail SAC/fone) ou qualquer conta/e-mail da linha; "@" inicial ignorado
{
  const row = {
    marca: { id: 'm1', nome: 'Loja Teste', slug: 'loja-teste', sac_email: 'sac@teste.com', sac_fone: '11999990000' },
    cells: {
      instagram: [{ conta: 'ig.loja', email: null, email_efetivo: 'sac@teste.com' }],
      tiktok: [{ conta: null, email: 'tt@outro.com', email_efetivo: 'tt@outro.com' }],
      youtube: [],
    },
  }
  assert.equal(H.rowMatches(row, 'loja teste'), true, 'nome')
  assert.equal(H.rowMatches(row, 'loja-teste'), true, 'slug')
  assert.equal(H.rowMatches(row, 'ig.loja'), true, 'conta')
  assert.equal(H.rowMatches(row, '@ig.loja'), true, 'busca com @ acha a conta gravada sem @')
  assert.equal(H.rowMatches(row, '@@IG.LOJA'), true, 'vários @ e caixa')
  assert.equal(H.rowMatches(row, 'sac@teste'), true, 'e-mail SAC da marca')
  assert.equal(H.rowMatches(row, 'tt@outro'), true, 'e-mail próprio da conta')
  assert.equal(H.rowMatches(row, '119999'), true, 'fone da marca')
  assert.equal(H.rowMatches(row, ''), true, 'vazio = tudo')
  assert.equal(H.rowMatches(row, 'nada-disso'), false)
}

// sincronizaEfetivos: mesma regra de routers/redes_sociais.py::rede_out
{
  const row = {
    marca: { sac_email: 'sac@m.x', sac_fone: '1199', has_sac_senha: true },
    cells: {
      instagram: [
        { email: null, fone: null, has_senha: false, email_efetivo: 'velho@x', fone_efetivo: '0', senha_origem: null, has_senha_efetiva: false },
        { email: 'ig@m.x', fone: '2288', has_senha: true, email_efetivo: null, fone_efetivo: null, senha_origem: null, has_senha_efetiva: false },
      ],
      youtube: [],
    },
  }
  H.sincronizaEfetivos(row)
  const [herda, propria] = row.cells.instagram
  assert.deepEqual([herda.email_efetivo, herda.fone_efetivo, herda.senha_origem, herda.has_senha_efetiva], ['sac@m.x', '1199', 'marca', true])
  assert.deepEqual([propria.email_efetivo, propria.fone_efetivo, propria.senha_origem, propria.has_senha_efetiva], ['ig@m.x', '2288', 'conta', true])
  row.marca.has_sac_senha = false
  row.marca.sac_email = null
  H.sincronizaEfetivos(row)
  assert.deepEqual([herda.email_efetivo, herda.senha_origem, herda.has_senha_efetiva], [null, null, false])
  assert.equal(propria.senha_origem, 'conta', 'senha própria não depende da marca')
}

// dotClass / VERIFICACAO_CURTO / verifLabel
{
  assert.equal(H.dotClass('em_andamento'), 'bg-amber-500')
  assert.equal(H.dotClass('recusado'), 'bg-red-500')
  assert.equal(H.dotClass('verificado'), 'bg-emerald-500')
  assert.equal(H.dotClass('nao_solicitado'), H.dotClass(null))
  assert.equal(H.dotClass('zzz'), H.dotClass('nao_solicitado'), 'status desconhecido cai no apagado')
  assert.equal(H.VERIFICACAO_CURTO.nao_solicitado, '', 'não solicitado: só o pontinho, sem texto')
  assert.ok(Object.values(H.VERIFICACAO_CURTO).every((s) => s.length <= 6), 'texto curto (cabe em 130px)')
  assert.equal(H.verifLabel('em_andamento'), 'em andamento')
  assert.equal(H.verifLabel(null), 'não solicitado')
  assert.equal(H.verifLabel('zzz'), 'zzz')
}

// senhaHintTexto: dirigida por senha_origem (spec v3.1)
{
  const base = { modo: 'edit', origem: 'marca', digitada: false, marcaTemSenha: true, revelada: null }
  assert.match(H.senhaHintTexto(base), /herdada da marca — digite para usar uma senha própria/)
  assert.match(H.senhaHintTexto({ ...base, origem: 'conta' }), /^senha própria desta conta$/)
  assert.match(H.senhaHintTexto({ ...base, origem: null }), /sem senha — nem na conta nem na marca/)
  assert.match(H.senhaHintTexto({ ...base, revelada: 'marca' }), /mostrando a senha da marca$/)
  assert.match(H.senhaHintTexto({ ...base, origem: 'conta', revelada: 'conta' }), /mostrando a senha da conta$/)
  assert.match(H.senhaHintTexto({ ...base, digitada: true }), /grava ao Salvar/)
  assert.equal(H.senhaHintTexto({ ...base, modo: 'create' }), 'em branco herda a senha da marca')
  assert.equal(H.senhaHintTexto({ ...base, modo: 'create', marcaTemSenha: false }), 'opcional')
}

// ---------------------------------------------------------------- script setup
// Tira os imports (resolvidos por parâmetro abaixo) e mantém o `await load()`
// de topo — a factory é async, então o grid inicial vem do api falso.
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  grid, loading, error, search, showGuia, colspan, load, filteredRows, marcasOpcoes, logoSrc, rowDaMarca,
  cellEdit, editValue, isEditing, isFlashed, inlineClass, startEdit, cancelEdit, commitEdit, zapTitle, MARCA_TEXT_COLS,
  revealed, revealedSenhas, toggleReveal, copySacSenha, copiedId, clearRevealed,
  senhaMarca, senhaMarcaValor, senhaMarcaVisible, senhaMarcaErr, openSenhaMarca, closeSenhaMarca,
  toggleSenhaMarca, saveSenhaMarca, limparSenhaMarca,
  modal, form, saving, modalErr, modo, marcaDoForm, emailPlaceholder, fonePlaceholder, urlSugerida, urlAbrir,
  openCreate, openEdit, closeModal, saveRede, removeRede,
  senhaVisible, senhaSalva, senhaOrigemRevelada, toggleSenha, copySenha, voltarHerdarSenha, senhaHint, senhaPlaceholder,
  conexao, tokenValor, tokenVisible, conectando, conexaoErr, conexaoContas, conexaoEscolha,
  abrirConexao, fecharConexao, conectar, desconectar, tokenPillTexto, tokenPillTitle, postagemAutoOn,
  conexaoPlataformaLabel, contaExternaId,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'watch', 'nextTick', 'onUnmounted',
  'definePageMeta', 'useApi', 'useCan',
  'setTimeout', 'clearTimeout', 'navigator', 'confirm',
  'TABS_CADASTROS', 'apiErrMsg', 'MARCAS_ERROS',
  'PLATAFORMAS', 'PLATAFORMA_LABELS', 'VERIFICACAO_GUIA', 'VERIFICACAO_LABELS', 'VERIFICACAO_STATUS', 'fmtFone', 'perfilUrl',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const FAKE = { senhaConta: 'senha-conta-teste-123', senhaMarca: 'senha-marca-teste-456' }
function marcaRef(over = {}) {
  return {
    id: 'm1', nome: 'Poofy', slug: 'poofy', ativo: true, classe: 'celular', funcao: 'locação', tipo: 'cel',
    obs: null, sac_fone: '11987654321', sac_email: 'sac@poofy.x', has_sac_senha: true,
    whatsapp_verificacao_status: 'nao_solicitado', whatsapp_verificacao_obs: null,
    has_logo: true, updated_at: '2026-09-15T10:00:00+00:00', ...over,
  }
}
function rede(over = {}) {
  return {
    id: 'r1', marca_id: 'm1', marca_nome: 'Poofy', plataforma: 'instagram', conta: 'poofy', usuario: null,
    url: null, email: null, fone: null, has_senha: false, email_efetivo: 'sac@poofy.x', fone_efetivo: '11987654321',
    has_senha_efetiva: true, senha_origem: 'marca', verificacao_status: 'nao_solicitado', verificacao_obs: null,
    obs: null, ativo: true,
    postagem_auto: false, postagem_max_dia: null, postagem_intervalo_min: null,
    has_token: false, token_status: null, token_conta_externa: null, token_expires_at: null,
    created_at: 't', updated_at: 't', ...over,
  }
}
const digits = (s) => (s == null ? null : String(s).replace(/\D/g, '') || null)
function gridOf(marcas, contas) {
  return {
    plataformas: [...redes.PLATAFORMAS],
    rows: marcas.map((m) => ({
      marca: { ...m },
      cells: Object.fromEntries(redes.PLATAFORMAS.map((p) => [p, contas.filter((r) => r.marca_id === m.id && r.plataforma === p).map((r) => ({ ...r }))])),
    })),
  }
}

// ConexaoOut falso: sem `external_user_id` escolhido devolve ok=false com as
// duas contas que o "token" enxerga (é o caso em que o operador escolhe);
// com a escolha, grava.
const CONTAS_EXTERNAS = [
  { page_id: 'p1', page_nome: 'Poofy Oficial', ig_user_id: 'ig1', ig_username: 'poofy.oficial' },
  { page_id: 'p2', page_nome: 'Poofy Outlet', ig_user_id: 'ig2', ig_username: 'poofy.outlet' },
]
function conexaoAmbigua(body) {
  if (!body.external_user_id) {
    return { ok: false, external_user_id: null, external_username: null, token_expires_at: null, contas: CONTAS_EXTERNAS }
  }
  const c = CONTAS_EXTERNAS.find((x) => x.ig_user_id === body.external_user_id)
  return {
    ok: true,
    external_user_id: body.external_user_id,
    external_username: c ? c.ig_username : null,
    token_expires_at: '2026-12-01T00:00:00+00:00',
    contas: CONTAS_EXTERNAS,
  }
}
const conexaoDireta = () => ({
  ok: true, external_user_id: 'ig1', external_username: 'poofy', token_expires_at: null, contas: [],
})

async function page({
  canEdit = true, marcas = [marcaRef()], contas = [rede()], confirmAnswer = true, postError = null,
  conexaoResp = conexaoDireta, conectarError = null,
} = {}) {
  const calls = []
  const timers = []
  const clipboard = []
  const unmountHooks = []
  const confirms = []
  // Corpos que chegaram no POST /conectar — é onde o token PODE aparecer.
  const conectarBodies = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/redes-sociais/grid') return Promise.resolve(gridOf(marcas, contas))
    if (/\/conectar$/.test(url)) {
      const r = contas.find((x) => `/api/redes-sociais/${x.id}/conectar` === url)
      assert.ok(r, `conta conhecida: ${url}`)
      if (opts?.method === 'DELETE') {
        Object.assign(r, { has_token: false, token_status: null, token_conta_externa: null, token_expires_at: null })
        return Promise.resolve(null)
      }
      assert.equal(opts?.method, 'POST', 'conectar é POST')
      conectarBodies.push(opts.body)
      if (conectarError) return Promise.reject(conectarError)
      const resp = conexaoResp(opts.body)
      if (resp.ok) {
        Object.assign(r, {
          has_token: true,
          token_status: 'ok',
          token_conta_externa: resp.external_username || resp.external_user_id,
          token_expires_at: resp.token_expires_at,
        })
      }
      return Promise.resolve(resp)
    }
    if (url.endsWith('/sac-senha')) return Promise.resolve({ senha: FAKE.senhaMarca, origem: 'marca' })
    if (url.endsWith('/senha')) {
      const r = contas.find((x) => `/api/redes-sociais/${x.id}/senha` === url)
      return Promise.resolve(r?.has_senha ? { senha: FAKE.senhaConta, origem: 'conta' } : { senha: FAKE.senhaMarca, origem: 'marca' })
    }
    if (opts?.method === 'DELETE') return Promise.resolve(null)
    if (opts?.method === 'PATCH' && url.startsWith('/api/redes-sociais/marca/')) {
      // MarcaSocialPatch → MarcaRef: fone só dígitos, senha vira has_sac_senha.
      const m = marcas.find((x) => `/api/redes-sociais/marca/${x.id}` === url)
      assert.ok(m, `marca conhecida: ${url}`)
      const { sac_senha, ...rest } = opts.body
      const upd = { ...m, ...rest }
      if ('sac_fone' in rest) upd.sac_fone = digits(rest.sac_fone)
      if ('sac_senha' in opts.body) upd.has_sac_senha = !!sac_senha
      Object.assign(m, upd)
      return Promise.resolve({ ...upd })
    }
    if (opts?.method === 'PATCH') {
      const r = contas.find((x) => `/api/redes-sociais/${x.id}` === url)
      assert.ok(r, `conta conhecida: ${url}`)
      const m = marcas.find((x) => x.id === r.marca_id)
      const { senha, ...rest } = opts.body
      const upd = { ...r, ...rest }
      if ('senha' in opts.body) upd.has_senha = !!senha
      upd.senha_origem = upd.has_senha ? 'conta' : m?.has_sac_senha ? 'marca' : null
      upd.has_senha_efetiva = upd.senha_origem !== null
      Object.assign(r, upd)
      return Promise.resolve({ ...upd })
    }
    if (opts?.method === 'POST') {
      if (postError) return Promise.reject(postError)
      return Promise.resolve(rede({ id: 'novo', ...opts.body, has_senha: !!opts.body.senha, senha: undefined }))
    }
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const fakeTimeout = (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length }
  const fakeClear = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true }
  const state = await factory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => unmountHooks.push(fn),
    () => {}, () => ({ api }), (resource, action) => Vue.ref(action === 'view' ? true : canEdit),
    fakeTimeout, fakeClear,
    { clipboard: { writeText: async (t) => { clipboard.push(t) } } }, (msg) => { confirms.push(msg); return confirmAnswer },
    [], apiError.apiErrMsg, apiError.MARCAS_ERROS,
    redes.PLATAFORMAS, redes.PLATAFORMA_LABELS, redes.VERIFICACAO_GUIA, redes.VERIFICACAO_LABELS, redes.VERIFICACAO_STATUS, redes.fmtFone, redes.perfilUrl,
  )
  return { state, calls, timers, clipboard, unmountHooks, confirms, marcas, contas, conectarBodies }
}
const settle = () => new Promise(setImmediate)
const senhaCalls = (calls) => calls.filter((c) => /\/senha$|\/sac-senha$/.test(c.url))
const patches = (calls) => calls.filter((c) => c.opts?.method === 'PATCH')
const marcasApiCalls = (calls) => calls.filter((c) => c.url.startsWith('/api/marcas'))

async function run() {
  // Carga inicial: GET /grid, 12 colunas, nenhuma senha buscada, busca no
  // cliente (marca, @conta, e-mail SAC, fone), colunas de texto da marca.
  {
    const { state: s, calls } = await page()
    assert.equal(calls[0].url, '/api/redes-sociais/grid')
    assert.equal(senhaCalls(calls).length, 0, 'a carga inicial não pode buscar senha')
    assert.equal(s.colspan.value, 4 + redes.PLATAFORMAS.length + 3)
    assert.deepEqual(s.MARCA_TEXT_COLS.map((c) => c.key), ['funcao', 'tipo', 'obs'])
    assert.equal(s.filteredRows.value.length, 1)
    for (const q of ['poo', '@poofy', 'sac@poofy', '1198765']) {
      s.search.value = q
      assert.equal(s.filteredRows.value.length, 1, `busca "${q}"`)
    }
    s.search.value = 'zzz'
    assert.equal(s.filteredRows.value.length, 0)
    s.search.value = ''
    assert.equal(s.marcasOpcoes.value[0].label, 'Poofy')
    assert.equal(s.logoSrc(s.grid.value.rows[0].marca), '/api/marcas/m1/logo?v=2026-09-15T10%3A00%3A00%2B00%3A00')
    assert.match(s.zapTitle(s.grid.value.rows[0].marca), /não solicitado — clique para alterar/)
  }

  // Senha da marca na célula: GET .../marca/{id}/sac-senha só no clique,
  // auto-oculta em 30 s, some ao recarregar / filtrar / desmontar; copiar
  // busca sem guardar.
  {
    const { state: s, calls, timers, clipboard, unmountHooks } = await page()
    await s.toggleReveal('m1')
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1/sac-senha')
    assert.equal(s.revealedSenhas.value.get('m1'), FAKE.senhaMarca)
    assert.ok(s.revealed.value.has('m1'))
    const hide = timers.find((t) => t.ms === 30_000)
    assert.ok(hide, 'auto-ocultar em 30 s')
    hide.fn()
    assert.equal(s.revealed.value.has('m1'), false)
    assert.equal(s.revealedSenhas.value.has('m1'), false)

    await s.toggleReveal('m1')
    await s.load()
    assert.equal(s.revealedSenhas.value.size, 0, 'recarregar descarta a senha revelada')
    await s.toggleReveal('m1')
    s.search.value = 'po'
    await Vue.nextTick()
    assert.equal(s.revealedSenhas.value.size, 0, 'filtrar descarta a senha revelada')
    await s.toggleReveal('m1')
    assert.equal(unmountHooks.length, 1)
    unmountHooks[0]()
    assert.equal(s.revealedSenhas.value.size, 0, 'onUnmounted descarta a senha revelada')

    const antes = senhaCalls(calls).length
    await s.copySacSenha('m1')
    assert.equal(senhaCalls(calls).length, antes + 1, 'copiar busca a senha')
    assert.deepEqual(clipboard, [FAKE.senhaMarca])
    assert.equal(s.revealedSenhas.value.size, 0, 'copiar não guarda a senha')
    assert.equal(s.copiedId.value, 'm1')
  }

  // Edição inline das colunas da MARCA: PATCH /api/redes-sociais/marca/{id}
  // (nunca /api/marcas), fone formatado no input, efetivos das contas
  // sincronizados, status do Zap NOT NULL, obs vazia → null, sem edit → nada.
  {
    const { state: s, calls } = await page()
    const row = s.grid.value.rows[0]
    await s.startEdit(row, 'sac_fone')
    assert.equal(s.editValue.value, '(11) 98765-4321', 'fone aparece formatado')
    await s.commitEdit()
    assert.equal(patches(calls).length, 0, 'sem alteração não há PATCH')
    await s.startEdit(row, 'sac_fone')
    s.editValue.value = '11 91234-5678'
    await s.commitEdit()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1')
    assert.deepEqual(calls.at(-1).opts.body, { sac_fone: '11 91234-5678' })
    assert.equal(row.marca.sac_fone, '11912345678')
    assert.equal(row.cells.instagram[0].fone_efetivo, '11912345678', 'conta que herda vê o fone novo')
    assert.ok(s.isFlashed('m1', 'sac_fone'))
    assert.equal(s.cellEdit.value, null)

    await s.startEdit(row, 'sac_email')
    s.editValue.value = 'novo@poofy.x'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { sac_email: 'novo@poofy.x' })
    assert.equal(row.cells.instagram[0].email_efetivo, 'novo@poofy.x')

    await s.startEdit(row, 'whatsapp_verificacao_status')
    s.editValue.value = 'em_andamento'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { whatsapp_verificacao_status: 'em_andamento' })
    assert.match(s.zapTitle(row.marca), /em andamento/)
    const n = patches(calls).length
    await s.startEdit(row, 'whatsapp_verificacao_status')
    s.editValue.value = ''
    await s.commitEdit()
    assert.equal(patches(calls).length, n, 'status vazio não salva (coluna NOT NULL)')

    await s.startEdit(row, 'whatsapp_verificacao_obs')
    s.editValue.value = ' protocolo 42 '
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { whatsapp_verificacao_obs: 'protocolo 42' })

    await s.startEdit(row, 'obs')
    s.editValue.value = '  '
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { obs: null })
    await s.startEdit(row, 'funcao')
    s.editValue.value = 'venda'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { funcao: 'venda' })
    assert.equal(row.marca.funcao, 'venda')

    assert.equal(marcasApiCalls(calls).length, 0, 'nada vai pra /api/marcas')
    assert.ok(patches(calls).every((c) => c.url.startsWith('/api/redes-sociais/marca/')))

    // Esc cancela sem PATCH
    const n2 = patches(calls).length
    await s.startEdit(row, 'tipo')
    s.editValue.value = 'x'
    s.cancelEdit()
    await s.commitEdit()
    assert.equal(patches(calls).length, n2)
    assert.deepEqual(s.inlineClass('m1', 'tipo', false)['cursor-pointer hover:bg-accent/30 rounded'], true)
  }

  // Só-view: nada de edição inline, mini-modal da senha nem criação.
  {
    const { state: s, calls } = await page({ canEdit: false })
    const row = s.grid.value.rows[0]
    await s.startEdit(row, 'sac_fone')
    assert.equal(s.cellEdit.value, null, 'sem edit não abre input')
    assert.equal(s.inlineClass('m1', 'sac_fone', false)['cursor-pointer hover:bg-accent/30 rounded'], false)
    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarca.value, null)
    s.openCreate('m1', 'tiktok')
    assert.equal(s.modal.value, null)
    s.openEdit(row.cells.instagram[0])
    assert.ok(s.modal.value, 'só-view abre a conta pra ler')
    await s.toggleSenha()
    assert.equal(senhaCalls(calls).length, 0, 'só-view nunca busca senha')
    assert.equal(s.form.value.senha, '')
    await s.copySenha()
    assert.equal(senhaCalls(calls).length, 0)
    await s.saveRede()
    assert.equal(patches(calls).length, 0, 'só-view não salva')
    assert.ok(!/clique para alterar/.test(s.zapTitle(row.marca)))
  }

  // Mini-modal "Senha das redes/SAC": olho revela a salva, Salvar sem mudar
  // não manda nada, senha nova → PATCH {sac_senha}, "limpar" → {sac_senha:
  // null} (com confirm) e as contas que herdavam ficam sem senha.
  {
    const { state: s, calls, confirms } = await page()
    const row = s.grid.value.rows[0]
    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarca.value.marca.id, 'm1')
    await s.saveSenhaMarca()
    assert.equal(patches(calls).length, 0, 'vazio não salva')
    assert.match(s.senhaMarcaErr.value, /digite a senha/)
    await s.toggleSenhaMarca()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1/sac-senha')
    assert.equal(s.senhaMarcaValor.value, FAKE.senhaMarca)
    assert.equal(s.senhaMarcaVisible.value, true)
    await s.saveSenhaMarca()
    assert.equal(patches(calls).length, 0, 'revelada e não alterada: nada a salvar')
    assert.equal(s.senhaMarca.value, null, 'fecha')

    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarcaValor.value, '', 'reabrir começa vazio')
    s.senhaMarcaValor.value = 'nova-senha-teste'
    await s.saveSenhaMarca()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1')
    assert.deepEqual(calls.at(-1).opts.body, { sac_senha: 'nova-senha-teste' })
    assert.equal(row.marca.has_sac_senha, true)
    assert.equal(s.senhaMarca.value, null)
    assert.equal(s.senhaMarcaValor.value, '', 'plaintext zerado ao fechar')

    await s.toggleReveal('m1')
    s.openSenhaMarca(row.marca)
    await s.limparSenhaMarca()
    assert.equal(confirms.length, 1)
    assert.deepEqual(calls.at(-1).opts.body, { sac_senha: null })
    assert.equal(row.marca.has_sac_senha, false)
    assert.equal(row.cells.instagram[0].senha_origem, null, 'conta que herdava fica sem senha')
    assert.equal(row.cells.instagram[0].has_senha_efetiva, false)
    assert.equal(s.revealedSenhas.value.size, 0, 'senha revelada da marca some depois do PATCH')

    // sem senha salva, "limpar" é no-op
    s.openSenhaMarca(row.marca)
    await s.limparSenhaMarca()
    assert.equal(confirms.length, 1)
    // confirm recusado → nada
    const { state: s2, calls: c2, confirms: cf2 } = await page({ confirmAnswer: false })
    s2.openSenhaMarca(s2.grid.value.rows[0].marca)
    await s2.limparSenhaMarca()
    assert.equal(cf2.length, 1)
    assert.equal(patches(c2).length, 0)
  }

  // Modal da conta — senha herdada da marca: placeholders "herda: …", olho
  // revela a EFETIVA com origem, Salvar não reenvia a revelada, sem
  // "voltar a herdar"; timer de 30 s; fechar zera.
  {
    const { state: s, calls, timers } = await page()
    const row = s.grid.value.rows[0]
    s.openEdit(row.cells.instagram[0])
    assert.equal(s.modo.value, 'edit')
    assert.equal(s.form.value.email, '', 'só o PRÓPRIO da conta no campo')
    assert.equal(s.emailPlaceholder.value, 'herda: sac@poofy.x')
    assert.equal(s.fonePlaceholder.value, 'herda: (11) 98765-4321')
    assert.equal(s.senhaPlaceholder.value, 'herda a senha da marca')
    assert.match(s.senhaHint.value, /herdada da marca/)
    assert.equal(s.urlSugerida.value, 'https://www.instagram.com/poofy/')
    await s.toggleSenha()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/r1/senha')
    assert.equal(s.form.value.senha, FAKE.senhaMarca)
    assert.equal(s.senhaOrigemRevelada.value, 'marca')
    assert.match(s.senhaHint.value, /mostrando a senha da marca/)
    assert.ok(timers.some((t) => t.ms === 30_000), 'modal auto-oculta em 30 s')
    await s.voltarHerdarSenha()
    assert.equal(patches(calls).length, 0, 'herdada: não há o que limpar')
    s.form.value.verificacao_status = 'em_andamento'
    s.form.value.verificacao_obs = 'protocolo 7'
    await s.saveRede()
    const p = patches(calls).at(-1)
    assert.equal(p.url, '/api/redes-sociais/r1')
    assert.ok(!('senha' in p.opts.body), 'senha revelada não é reenviada')
    assert.equal(p.opts.body.verificacao_status, 'em_andamento')
    assert.equal(p.opts.body.verificacao_obs, 'protocolo 7')
    assert.equal(p.opts.body.ativo, true)
    assert.equal(p.opts.body.email, null, 'vazio = continua herdando')
    assert.equal(s.modal.value, null, 'fecha e recarrega')
    assert.equal(calls.at(-1).url, '/api/redes-sociais/grid')
    assert.equal(s.form.value.senha, '', 'plaintext zerado')

    // senha digitada por cima da herdada → vira própria
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    s.form.value.senha = 'propria-teste'
    assert.match(s.senhaHint.value, /grava ao Salvar/)
    await s.saveRede()
    assert.equal(patches(calls).at(-1).opts.body.senha, 'propria-teste')
    s.closeModal()
    assert.equal(s.form.value.senha, '')
  }

  // Modal da conta — senha PRÓPRIA: "voltar a herdar a da marca" manda
  // senha:null com confirm e atualiza a conta no grid.
  {
    const { state: s, calls, confirms } = await page({ contas: [rede({ has_senha: true, senha_origem: 'conta', email: 'ig@poofy.x', email_efetivo: 'ig@poofy.x' })] })
    const r = s.grid.value.rows[0].cells.instagram[0]
    s.openEdit(r)
    assert.equal(s.form.value.email, 'ig@poofy.x')
    assert.equal(s.senhaPlaceholder.value, 'branco = manter a própria')
    assert.match(s.senhaHint.value, /^senha própria desta conta$/)
    await s.toggleSenha()
    assert.equal(s.form.value.senha, FAKE.senhaConta)
    assert.equal(s.senhaOrigemRevelada.value, 'conta')
    await s.voltarHerdarSenha()
    assert.equal(confirms.length, 1)
    assert.deepEqual(calls.at(-1).opts.body, { senha: null })
    assert.equal(s.modal.value.rede.senha_origem, 'marca', 'volta a herdar')
    assert.equal(s.grid.value.rows[0].cells.instagram[0].senha_origem, 'marca', 'grid atualizado sem recarregar')
    assert.equal(s.form.value.senha, '')
    assert.equal(s.senhaSalva.value, null)
    assert.match(s.senhaHint.value, /herdada da marca/)
  }

  // Criar: "+" da célula pré-seleciona marca e plataforma; POST sem ativo,
  // com verificacao_status default; hint "em branco herda". Excluir: DELETE
  // com confirm.
  {
    const { state: s, calls, confirms } = await page()
    s.openCreate('m1', 'tiktok')
    assert.equal(s.modo.value, 'create')
    assert.equal(s.form.value.plataforma, 'tiktok')
    assert.equal(s.marcaDoForm.value.id, 'm1')
    assert.equal(s.senhaHint.value, 'em branco herda a senha da marca')
    assert.equal(s.senhaPlaceholder.value, 'em branco herda a senha da marca')
    s.form.value.conta = '@poofy.tt'
    await s.saveRede()
    const post = calls.find((c) => c.opts?.method === 'POST')
    assert.equal(post.url, '/api/redes-sociais')
    assert.ok(!('ativo' in post.opts.body) && !('senha' in post.opts.body))
    assert.equal(post.opts.body.verificacao_status, 'nao_solicitado')
    assert.equal(post.opts.body.marca_id, 'm1')
    assert.equal(s.modal.value, null)

    s.openCreate()
    await s.saveRede()
    assert.equal(s.modalErr.value, 'escolha a marca')

    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    await s.removeRede()
    assert.equal(confirms.length, 1)
    assert.match(confirms[0], /@poofy \(Instagram\) de Poofy/)
    assert.equal(calls.find((c) => c.opts?.method === 'DELETE').url, '/api/redes-sociais/r1')
    assert.equal(s.modal.value, null)
  }

  // Erro da API vira mensagem legível (apiErrMsg + MARCAS_ERROS).
  {
    const { state: s } = await page({ postError: { data: { detail: { code: 'rede_social_conta_conflict' } } } })
    s.openCreate('m1', 'youtube')
    await s.saveRede()
    assert.equal(s.modalErr.value, 'Essa conta já está cadastrada nessa plataforma')
    assert.ok(s.modal.value, 'modal fica aberto com o erro')
  }

  // ------------------------------------------------ publicação automática (v3.2)

  // Bloco do modal: interruptor + tetos. Campo vazio = null EXPLÍCITO no
  // PATCH (volta a herdar o padrão do servidor); pill mostra o @ da
  // credencial, nunca o token.
  {
    const { state: s, calls } = await page({
      contas: [rede({ postagem_auto: true, postagem_max_dia: 3, has_token: true, token_status: 'ok', token_conta_externa: 'poofy' })],
    })
    const r = s.grid.value.rows[0].cells.instagram[0]
    assert.equal(s.postagemAutoOn(r), true, 'indicadorzinho do grid ligado (token + interruptor)')
    s.openEdit(r)
    assert.equal(s.form.value.postagem_auto, true)
    assert.equal(s.form.value.postagem_max_dia, 3)
    assert.equal(s.form.value.postagem_intervalo_min, '', 'null vira campo vazio (herda o padrão)')
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'conectado como @poofy')
    assert.match(s.tokenPillTitle.value, /cifrada no servidor/)
    s.form.value.postagem_max_dia = ''
    s.form.value.postagem_intervalo_min = 45
    await s.saveRede()
    const p = patches(calls).at(-1)
    assert.equal(p.url, '/api/redes-sociais/r1')
    assert.equal(p.opts.body.postagem_auto, true)
    assert.equal(p.opts.body.postagem_max_dia, null, 'campo vazio volta pro padrão do servidor')
    assert.equal(p.opts.body.postagem_intervalo_min, 45)

    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    assert.equal(s.form.value.postagem_intervalo_min, 45, 'teto salvo volta no campo')
    s.form.value.postagem_auto = false
    await s.saveRede()
    assert.equal(patches(calls).at(-1).opts.body.postagem_auto, false, 'desligar o interruptor é PATCH explícito')
    assert.equal(s.postagemAutoOn(s.grid.value.rows[0].cells.instagram[0]), false)
  }

  // Conectar (caminho feliz): token vai só no CORPO do POST /conectar, some
  // do campo assim que grava, e a linha do grid passa a mostrar a credencial.
  {
    const { state: s, calls, conectarBodies } = await page()
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'sem token')
    assert.match(s.tokenPillTitle.value, /o robô não publica sozinho/)
    s.abrirConexao()
    assert.equal(s.conexao.value.rede.id, 'r1')
    assert.equal(s.tokenValor.value, '', 'sub-modal abre com o campo vazio')
    assert.equal(s.tokenVisible.value, false, 'token começa mascarado')
    assert.equal(s.conexaoPlataformaLabel.value, 'Instagram')
    await s.conectar()
    assert.equal(conectarBodies.length, 0, 'sem token não chama a API')
    assert.match(s.conexaoErr.value, /cole o token/)
    s.tokenValor.value = `  ${TOKEN_FALSO}  `
    await s.conectar()
    assert.deepEqual(conectarBodies, [{ access_token: TOKEN_FALSO, external_user_id: null }], 'token no corpo, sem espaços')
    assert.equal(calls.at(-1).url, '/api/redes-sociais/r1/conectar')
    assert.equal(s.conexao.value, null, 'fecha ao conectar')
    assert.equal(s.tokenValor.value, '', 'plaintext do token zerado depois do POST')
    assert.equal(s.modal.value.rede.has_token, true)
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'conectado como @poofy')
    assert.equal(s.grid.value.rows[0].cells.instagram[0].has_token, true, 'grid atualizado sem recarregar')
    // O token não aparece em nenhuma URL nem em nenhum outro corpo.
    for (const c of calls) {
      assert.ok(!c.url.includes(TOKEN_FALSO), 'token nunca na URL')
      if (!/\/conectar$/.test(c.url)) {
        assert.ok(!JSON.stringify(c.opts || {}).includes(TOKEN_FALSO), `token não vaza em ${c.url}`)
      }
    }
    // Com credencial, ligar o interruptor acende o indicadorzinho do grid.
    s.form.value.postagem_auto = true
    await s.saveRede()
    assert.equal(s.postagemAutoOn(s.grid.value.rows[0].cells.instagram[0]), true)
  }

  // Conectar (ok=false): o backend não soube casar o @ e devolveu o que o
  // token enxerga — o operador escolhe e o MESMO token é reenviado com
  // external_user_id.
  {
    const { state: s, conectarBodies } = await page({ conexaoResp: conexaoAmbigua })
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    s.abrirConexao()
    s.tokenValor.value = TOKEN_FALSO
    await s.conectar()
    assert.equal(s.conexao.value.rede.id, 'r1', 'sub-modal continua aberto pra escolha')
    assert.deepEqual(s.conexaoContas.value.map((c) => c.ig_username), ['poofy.oficial', 'poofy.outlet'])
    assert.match(s.conexaoErr.value, /escolha qual conta/)
    assert.equal(s.modal.value.rede.has_token, false, 'nada gravado enquanto não escolhe')
    assert.equal(s.tokenValor.value, TOKEN_FALSO, 'campo mantém o token só porque o reenvio precisa dele')
    await s.conectar()
    assert.equal(conectarBodies.length, 1, 'sem escolher não reenvia')
    assert.equal(s.contaExternaId(s.conexaoContas.value[1], 'instagram'), 'ig2')
    s.conexaoEscolha.value = 'ig2'
    await s.conectar()
    assert.equal(conectarBodies.length, 2)
    assert.deepEqual(conectarBodies[1], { access_token: TOKEN_FALSO, external_user_id: 'ig2' })
    assert.equal(s.modal.value.rede.token_conta_externa, 'poofy.outlet')
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'conectado como @poofy.outlet')
    assert.equal(s.tokenValor.value, '', 'token zerado depois de gravar')
    assert.equal(s.conexaoContas.value.length, 0, 'as opções somem junto')
  }

  // O token nunca sobrevive a fechar / recarregar / desmontar.
  {
    const { state: s, unmountHooks } = await page()
    const abre = () => {
      s.openEdit(s.grid.value.rows[0].cells.instagram[0])
      s.abrirConexao()
      s.tokenValor.value = TOKEN_FALSO
    }
    abre()
    s.fecharConexao()
    assert.equal(s.tokenValor.value, '', 'cancelar zera')
    abre()
    s.closeModal()
    assert.equal(s.conexao.value, null, 'fechar a conta fecha o sub-modal')
    assert.equal(s.tokenValor.value, '', 'fechar a conta zera o token')
    abre()
    await s.load()
    assert.equal(s.tokenValor.value, '', 'recarregar zera o token')
    abre()
    unmountHooks[0]()
    assert.equal(s.tokenValor.value, '', 'sair da página zera o token')
  }

  // Erros do /conectar: código novo (em memória) e o 422 do validador.
  {
    const { state: s } = await page({ conectarError: { data: { detail: { code: 'token_recusado_pela_meta', erro: 'OAuth' } } } })
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    s.abrirConexao()
    s.tokenValor.value = TOKEN_FALSO
    await s.conectar()
    assert.match(s.conexaoErr.value, /^A Meta recusou esse token/)
    assert.ok(s.conexao.value, 'sub-modal fica aberto com o erro')
    assert.equal(s.modal.value.rede.has_token, false)
    assert.equal(s.conectando.value, false, 'botão volta a funcionar')

    const { state: s2 } = await page({
      conectarError: { data: { detail: [{ loc: ['body', 'access_token'], msg: 'Value error, token_invalido' }] } },
    })
    s2.openEdit(s2.grid.value.rows[0].cells.instagram[0])
    s2.abrirConexao()
    s2.tokenValor.value = 'curto'
    await s2.conectar()
    assert.equal(s2.conexaoErr.value, 'access_token: Token inválido — cole o token inteiro, sem espaços')
  }

  // Desconectar: confirm + DELETE /conectar; o robô para de publicar sozinho.
  {
    const { state: s, calls, confirms } = await page({
      contas: [rede({ has_token: true, token_conta_externa: 'poofy', postagem_auto: true })],
    })
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    await s.desconectar()
    assert.equal(confirms.length, 1)
    assert.match(confirms.at(-1), /@poofy/)
    assert.equal(calls.filter((c) => c.opts?.method === 'DELETE').at(-1).url, '/api/redes-sociais/r1/conectar')
    assert.equal(s.modal.value.rede.has_token, false)
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'sem token')
    assert.equal(
      s.postagemAutoOn(s.grid.value.rows[0].cells.instagram[0]), false,
      'sem credencial o robô não publica, mesmo com o interruptor ligado',
    )
    assert.ok(s.modal.value, 'a conta continua aberta')

    const { state: s2, calls: c2 } = await page({ contas: [rede({ has_token: true })], confirmAnswer: false })
    s2.openEdit(s2.grid.value.rows[0].cells.instagram[0])
    await s2.desconectar()
    assert.equal(c2.filter((c) => /\/conectar$/.test(c.url)).length, 0, 'confirm recusado não desconecta')
  }

  // Só-view não conecta nem desconecta.
  {
    const { state: s, calls } = await page({ canEdit: false, contas: [rede({ has_token: true, token_conta_externa: 'poofy' })] })
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    assert.equal(s.tokenPillTexto(s.modal.value.rede), 'conectado como @poofy', 'só-view vê o estado')
    s.abrirConexao()
    assert.equal(s.conexao.value, null, 'só-view não abre o sub-modal do token')
    await s.desconectar()
    assert.equal(calls.filter((c) => /\/conectar$/.test(c.url)).length, 0)
  }
}

run().then(() => {
  console.log('PASS: SFC parse + template compile; higiene de senha e do token; helpers puros; script setup com api falso (grid, inline PATCH /marca, senha da marca, modal da conta, guia, publicação automática: conectar/escolher conta/desconectar e tetos por conta)')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
