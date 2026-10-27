// node tests/atendimento-zap-numeros.cjs — a tela "Números do WhatsApp" (Zap,
// Onda 4 de docs/zap/desenho.md, 27/10/2026): `components/AtendimentoZapNumeros.vue`
// dentro de "Lojas e modo" (`AtendimentoCanais.vue`), só para administrador.
// O que a tela tem de garantir:
//  - as ROTAS que ela chama existem no backend (`routers/atendimento_zap.py`,
//    `router_numeros`, registrado no `main.py`);
//  - as listas paralelas batem com o backend: etapas, etapas que mandam,
//    casos da migração (A/A2/B), códigos da "Conexão" e o "onde" do webhook em
//    outro lugar;
//  - os SEGREDOS (chave secreta do app, token, PIN): campo mascarado
//    (`WebkitTextSecurity`), sem preenchimento automático, só no corpo do
//    POST/PATCH com o nome que a máscara do Histórico esconde (`app_secret`,
//    `access_token`, `pin`), zerados ao fechar o modal, nunca guardados no
//    navegador nem no console;
//  - o código de verificação e o PIN só aparecem sob clique (GET que fica no
//    Histórico) — e o "gerar outro" não mostra o código;
//  - o passo a passo em português simples (o que copiar de onde na Meta) e os
//    botões da §8.4 com o nome que o Eduardo lê;
//  - "Inscrever", "Pausar todos", ligar o envio, mudar a etapa e "Registrar"
//    pedem confirmação; "Pausar" não espera chave;
//  - só admin vê: o "Lojas e modo" monta a tela com `verZap` (admin) e os
//    botões com `isAdmin` (admin que mexe).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel) })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  new Function('exports', 'require', transpile(compiled.code))({}, require)
  return descriptor
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return src.slice(a, b)
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
// Uma tupla de strings do Python: `NOME = (\n "a",\n "b",\n)` ou numa linha.
function tuplaDe(src, nome) {
  const m = new RegExp(`^${nome}(?:: [^=]+)? = \\(([\\s\\S]*?)\\)`, 'm').exec(src)
  assert.ok(m, `tupla ${nome}`)
  return [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1])
}

const desc = sfc('../components/AtendimentoZapNumeros.vue')
const Z = exportsDe(desc)
const setup = desc.scriptSetup.content
const tpl = desc.template.content
const rotasPy = api('routers/atendimento_zap.py')
const mainPy = api('main.py')

// ------------------------------------------------ as rotas existem no backend
{
  assert.match(mainPy, /app\.include_router\(atendimento_zap_router\.router_numeros\)/, 'o router da Onda 4 registrado')
  const doBackend = new Set(
    [...rotasPy.matchAll(/@router_numeros\.(get|post|patch|delete)\("([^"]+)"/g)].map((m) => `/api/atendimento/zap${m[2].replace(/\{[^}]+\}/g, 'X')}`),
  )
  for (const [nome, fn] of Object.entries(Z.ROTAS)) {
    const url = fn('X')
    assert.ok(doBackend.has(url), `ROTAS.${nome} = ${url} existe no backend`)
  }
  // Os ids vão escapados (nada de barra solta vinda do servidor).
  assert.equal(Z.ROTAS.numero('a/b'), '/api/atendimento/zap/numeros/a%2Fb')
  // Toda chamada da tela usa as ROTAS (nenhum endereço escrito à mão).
  const chamadas = [...setup.matchAll(/api<?[^(]*\(\s*([^,)]+)/g)].map((m) => m[1].trim())
  assert.ok(chamadas.length >= 15, 'a tela chama a API')
  for (const c of chamadas) assert.match(c, /^ROTAS\./, `chamada pelas ROTAS: ${c}`)
}

// ------------------------------------------------ listas paralelas = backend
{
  const modelo = api('models/atendimento_zap.py')
  assert.deepEqual(Z.ETAPAS.map((e) => e.value), tuplaDe(modelo, 'ZAP_NUMERO_ETAPAS'), 'as etapas, na mesma ordem')
  assert.ok(Z.ETAPAS.every((e) => e.label && e.hint), 'toda etapa com nome e explicação')
  assert.equal(Z.etapaInfo('ativo').label, 'No DaVinci')
  assert.deepEqual(Z.CASOS.map((c) => c.value), tuplaDe(modelo, 'ZAP_NUMERO_CASOS'), 'os casos A/A2/B')
  assert.match(Z.CASOS.find((c) => c.value === 'A2').label, /NÃO registrar/)
  const zapPy = api('services/atendimento/zap.py')
  assert.deepEqual(Z.ETAPAS_QUE_ENVIAM, tuplaDe(zapPy, 'ETAPAS_QUE_ENVIAM'), 'as etapas que mandam')
  assert.equal(Z.podeLigarEnvio({ etapa: 'migrando' }), false)
  assert.equal(Z.podeLigarEnvio({ etapa: 'ativo' }), true)
  // Os códigos da "Conexão" (`zap_saude.conexao`): todos têm nome.
  const saudePy = api('services/atendimento/zap_saude.py')
  const conexao = trecho(saudePy, 'def conexao(', '\n# ──')
  const codigos = [...conexao.matchAll(/return "([a-z_]+)", "/g)].map((m) => m[1])
  assert.ok(codigos.length >= 8, 'os códigos da conexão')
  for (const c of codigos) assert.ok(Z.CONEXAO[c], `conexão ${c} com nome na tela`)
  assert.equal(Z.conexaoInfo('conectado').label, 'Conectado')
  // O "onde" do webhook em outro lugar (`zap_cadastro.webhook_em_outro_lugar`).
  const cadastroPy = api('services/atendimento/zap_cadastro.py')
  const onde = trecho(cadastroPy, 'def webhook_em_outro_lugar(', '\n\n\n')
  const ondes = [...(/for onde in \(([^)]*)\)/.exec(onde) || [])[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(Object.keys(Z.WEBHOOK_OUTRO).sort(), ondes.sort())
}

// ------------------------------------------------ as réguas da tabela
{
  assert.equal(Z.respostasTexto({ respostas_mes: 800 }), '800 de 1.000 grátis')
  assert.equal(Z.RESPOSTAS_GRATIS, 1000)
  assert.equal(Z.tetoPerto({ enviados_hoje: 69, teto_efetivo: 100 }), false)
  assert.equal(Z.tetoPerto({ enviados_hoje: 70, teto_efetivo: 100 }), true, 'aviso em 70% do teto')
  assert.equal(Z.tetoPerto({ enviados_hoje: 5, teto_efetivo: 0 }), false)
  assert.deepEqual(
    ['GREEN', 'YELLOW', 'RED', null].map((q) => Z.qualidadeInfo(q).label),
    ['Verde', 'Amarela', 'Vermelha', '—'],
  )
  // O nome de exibição em português (o número de teste vem "AVAILABLE_WITHOUT_REVIEW").
  assert.equal(Z.nomeStatusTexto('APPROVED'), 'aprovado')
  assert.equal(Z.nomeStatusTexto('available_without_review'), 'liberado sem revisão')
  assert.equal(Z.nomeStatusTexto('ALGO_NOVO'), 'algo novo')
}

// ------------------------------------------------ os segredos
{
  assert.equal(Z.CAMPO_SEGREDO.autocomplete, 'off')
  assert.ok('data-1p-ignore' in Z.CAMPO_SEGREDO && Z.CAMPO_SEGREDO['data-lpignore'] === 'true')
  assert.match(setup, /WebkitTextSecurity: 'disc'/, 'mascarado por padrão')
  for (const campo of ['chaveSecreta', 'tokenValor', 'pinValor']) {
    const input = new RegExp(`<Input v-model="${campo}"[^>]*>`).exec(tpl)
    assert.ok(input, `campo ${campo}`)
    assert.match(input[0], /v-bind="CAMPO_SEGREDO"/, `${campo} sem preenchimento automático`)
    assert.match(input[0], /:style="estiloSegredo"/, `${campo} mascarado`)
    assert.ok(!/type="password"/.test(input[0]), `${campo}: texto mascarado, não password (o navegador não oferece salvar)`)
  }
  // Zerados ao fechar (e ao abrir outro modal).
  const zerar = trecho(setup, 'function zerarSegredos()', '\n}\n')
  for (const campo of ['chaveSecreta', 'tokenValor', 'pinValor']) assert.match(zerar, new RegExp(`${campo}\\.value = ''`))
  assert.match(trecho(setup, 'function fecharModal()', '\n}\n'), /zerarSegredos\(\)/)
  assert.match(trecho(setup, 'function abrir(', '\n}\n'), /zerarSegredos\(\)/)
  // Só no corpo do POST/PATCH, com o nome que a máscara do Histórico esconde.
  assert.match(setup, /app_secret: chaveSecreta\.value/)
  assert.match(setup, /access_token: tokenValor\.value/)
  assert.match(setup, /corpo\.pin = pinValor\.value\.trim\(\)/)
  assert.match(setup, /pin: pinValor\.value\.trim\(\)/)
  const mascara = api('historico/mascara.py')
  assert.match(mascara, /"app_secret"/, 'app_secret mascarado no Histórico')
  assert.match(mascara, /\|token/, 'token mascarado no Histórico')
  assert.match(mascara, /\|pin\)/, 'pin mascarado no Histórico')
  const schemas = api('schemas/atendimento_zap.py')
  assert.match(trecho(schemas, 'class AppZapIn', '\n\n\n'), /app_secret: str/)
  assert.match(trecho(schemas, 'class ContaZapIn', '\n\n\n'), /access_token: str/)
  assert.match(trecho(schemas, 'class NumeroZapPatch', '\n\n\n'), /pin: str \| None/)
  // Nada de segredo guardado no navegador ou no console.
  for (const proibido of ['localStorage', 'sessionStorage', 'console.']) assert.ok(!setup.includes(proibido), proibido)
  // O código de verificação e o PIN só pelo GET que revela (fica no Histórico).
  assert.match(trecho(setup, 'async function mostrarVerify', '\n}\n'), /api<\{ verify_token: string \}>\(ROTAS\.verifyToken\(app\.id\)\)/)
  assert.ok(!/verify_token/.test(trecho(setup, 'async function gerarOutroVerify', '\n}\n')), 'gerar outro não mostra o código')
  assert.match(trecho(setup, 'async function mostrarPin', '\n}\n'), /api<\{ pin: string \| null \}>\(ROTAS\.pin\(n\.id\)\)/)
  const nomes = api('historico/nomes.py')
  assert.match(nomes, /"\/api\/atendimento\/zap\/apps\/\{app_ref\}\/verify-token",\s*\): "viu o código de verificação do webhook do WhatsApp"/)
  assert.match(nomes, /"\/api\/atendimento\/zap\/numeros\/\{numero_ref\}\/pin"\): "viu o PIN do número do WhatsApp"/)
}

// ------------------------------------------------ confirmações
{
  for (const fn of ['async function inscrever', 'async function pausarTodos', 'async function mudarEtapa', 'async function gerarOutroVerify']) {
    assert.match(trecho(setup, fn, '\n}\n'), /confirm\(/, `${fn} pede confirmação`)
  }
  // Ligar o envio pede confirmação; desligar é o "pausar" (na hora).
  const envio = trecho(setup, 'async function mudarEnvio', '\n}\n')
  assert.match(envio, /if \(ligar\) \{[\s\S]*confirm\(/)
  assert.match(envio, /await pausar\(n\)/)
  // Registrar: modal próprio, "1 tentativa", e o aviso de parar.
  assert.match(tpl, /Registrar \(1 tentativa\)/)
  assert.match(tpl, /fale com o suporte da Meta/)
  assert.match(tpl, /v-if="n\.registro\.pode"/, 'o botão Registrar só quando o backend libera')
  // Pausar não depende da chave do cadastro (só de quem mexe).
  assert.match(tpl, /:disabled="!podeMexer \|\| ocupado === 'pausar-todos' \|\| !totais\.enviando"/)
  assert.match(tpl, /v-if="n\.envio_ligado" size="sm" variant="outline" class="h-7 text-xs" :disabled="!podeMexer"/)
}

// ------------------------------------------------ o passo a passo e os nomes
{
  for (const frase of [
    'Chave secreta do app', 'ID do app', 'Configurações do app → Básico', 'Usuários do sistema',
    'funcionário', 'Nunca', 'whatsapp_business_messaging', 'whatsapp_business_management',
    'WhatsApp → Configuração → Webhook', 'Verificar e salvar', 'ID da conta do WhatsApp',
  ]) assert.ok(tpl.includes(frase), `o passo a passo fala de "${frase}"`)
  // Os campos a marcar vêm do backend (a mesma lista do §10.2).
  assert.match(tpl, /tela\.campos_do_webhook\.join\(', '\)/)
  for (const botao of [
    'Cadastrar app', 'Cadastrar conta do WhatsApp', 'Procurar números', 'Cadastrar este número',
    'Inscrever', 'Conferir inscrição', 'Testar conexão', 'Pausar envio', 'Pausar todos', 'Trocar token',
    'Trocar chave secreta', 'Mostrar código de verificação', 'Gerar outro', 'Mostrar PIN',
    'Reprocessar ignorados', 'Copiar', 'Editar',
  ]) assert.ok(tpl.includes(botao), `botão "${botao}"`)
  // As colunas da §8.4.
  for (const coluna of ['Número', 'Etapa', 'Conexão', 'Último recebido', 'Último erro', 'Qualidade', 'Respostas no mês', 'Envio', 'Modo']) {
    assert.ok(tpl.includes(`>${coluna}</th>`), `coluna ${coluna}`)
  }
  // Sem jargão nos rótulos principais (WABA/pnid só nas notas técnicas do backend).
  assert.ok(!/>[^<]*\bWABA\b[^<]*</.test(tpl), 'a tela diz "conta do WhatsApp", não WABA')
  assert.ok(!/phone_number_id/.test(tpl.replace(/n\.phone_number_id|phone_number_id"/g, '')), 'nada de phone_number_id na tela')
}

// ------------------------------------------------ nasce desligada
{
  // Sem a chave do cadastro e sem nada cadastrado: só uma linha (deploy não liga nada).
  assert.match(setup, /const desligadaEVazia = computed\(\(\) => !!tela\.value && !tela\.value\.chaves\.cadastro && !tela\.value\.apps\.length\)/)
  assert.match(tpl, /<div v-if="desligadaEVazia"[\s\S]*?desligado no servidor[\s\S]*?<\/div>\s*<section v-else/)
  // Com a chave desligada, tudo o que grava ou fala com a Meta fica desabilitado (`mexe`).
  // Os da tela (os do modal só abrem por um destes).
  const daTela = tpl.slice(0, tpl.indexOf('<!-- Os modais'))
  const botoes = [...daTela.matchAll(/<Button\b([^>]*)>([\s\S]*?)<\/Button>/g)]
  for (const botao of ['Cadastrar app', 'Inscrever', 'Conferir inscrição', 'Procurar números', 'Testar conexão', 'Trocar token', 'Trocar chave secreta']) {
    const achados = botoes.filter((b) => b[2].includes(botao))
    assert.ok(achados.length, `botão ${botao}`)
    for (const b of achados) assert.match(b[1], /:disabled="!mexe/, `${botao} depende da chave do cadastro`)
  }
}

// ------------------------------------------------ só admin vê
{
  const canais = sfc('../components/AtendimentoCanais.vue')
  assert.match(canais.template.content, /<LazyAtendimentoZapNumeros v-if="verZap" :pode-mexer="isAdmin" \/>/)
  assert.match(canais.scriptSetup.content, /verZap\?: boolean/)
  const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
  assert.match(pagina, /<LazyAtendimentoCanais [^>]*:ver-zap="ehAdmin"/)
  // E o backend dá 404 para quem não é admin (a mesma decisão).
  assert.match(rotasPy, /if user\.role != UserRole\.ADMIN or not acesso\.pode_ver\(user\):\s*raise HTTPException\(404/)
}

// ------------------------------------------------ revisão de 27/10/2026
{
  // Celulares de teste: o espaço é do número (um celular colado como o WhatsApp mostra).
  assert.deepEqual(Z.celularesDeTeste('+55 47 98417-7571'), ['5547984177571'])
  assert.deepEqual(Z.celularesDeTeste('5511999990001, 5511999990002;5511999990003\n5511999990004'), ['5511999990001', '5511999990002', '5511999990003', '5511999990004'])
  assert.match(setup, /destinos_teste: celularesDeTeste\(numeroForm\.destinos\)/)
  // O motivo do Registrar também no número de teste (etapa Teste).
  const reg = { pode: false, motivo: 'x', tentativas_72h: 0 }
  assert.equal(Z.mostraMotivoDoRegistro({ etapa: 'teste', teste: true, registro: reg }), true)
  assert.equal(Z.mostraMotivoDoRegistro({ etapa: 'teste', teste: false, registro: reg }), false)
  assert.equal(Z.mostraMotivoDoRegistro({ etapa: 'migrando', teste: false, registro: reg }), true)
  // O fundo escuro não fecha o modal enquanto confere na Meta (o erro sumiria).
  assert.match(tpl, /@click\.self="!salvando && fecharModal\(\)"/)
  // O "pausar" que falhou relê a tela (a caixa desmarcada não mente).
  assert.match(trecho(setup, 'async function pausar(', '\n}\n'), /catch \(e: any\) \{[\s\S]*await carregar\(\)/)
  // O app de TESTE em produção (a trava "app de teste só cadastra número de teste").
  assert.match(setup, /teste: appForm\.teste/)
  // LGPD: o pedido de exclusão pelo telefone, com confirmação.
  assert.match(trecho(setup, 'async function apagarPorTelefone', '\n}\n'), /confirm\(/)
  assert.ok(tpl.includes('Pedido de exclusão de dados (LGPD)'))
  const nomes = api('historico/nomes.py')
  assert.match(nomes, /"\/api\/atendimento\/zap\/contatos\/apagar-por-telefone",\s*\): "apagou/)
  assert.match(nomes, /atendimento\/zap\/\(conversas\|contatos\)\//, 'o corpo (o telefone) nunca no Histórico')
}

console.log('atendimento-zap-numeros: ok')
