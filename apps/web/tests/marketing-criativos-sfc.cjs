// Run from apps/web: node tests/marketing-criativos-sfc.cjs
// Marketing › Criativos — robô de postagem (Eduardo, 15/09/2026): parse +
// compileTemplate do componente (molde de redes-sociais-sfc.cjs), higiene
// estática do template (nenhum token/senha passa por aqui; link do post com
// rel=noopener; cancelar pede confirm; datetime-local presente; contador da
// legenda) e os helpers PUROS do modal — montagem do corpo do POST, conversão
// BRT → ISO com offset explícito e tradução dos motivos de uma conta não
// poder receber post. Só dados FALSOS aqui; nenhuma chamada de rede.
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
const filename = path.join(__dirname, '../components/MarketingCriativos.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({
  source: descriptor.template.content,
  filename,
  id: 'marketing-criativos-check',
})
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
const apiError = loadLib('../lib/apiError.ts')
const dateLib = loadLib('../lib/date.ts')
const redes = loadLib('../lib/redesSociais.ts')

// ---------------------------------------------------------------- higiene estática
// O token da conta (redes_sociais_tokens) nunca sai por API nenhuma — muito
// menos chega ao template. Senha idem: esta tela não mexe com credencial.
assert.ok(
  !/\b(token|senha|password|secret|credential)\b/i.test(tpl),
  'nenhum token/senha/credencial no template',
)
assert.ok(!/access_token|token_enc|senha_enc/.test(script), 'o front nunca lê token/senha cifrados')

// Link do post publicado abre fora, sem dar window.opener pra página de destino.
assert.match(tpl, /target="_blank"\s+rel="noopener"/, 'link do post com rel=noopener')
assert.match(tpl, /:href="p\.post_url"/, 'a pill vira link quando tem post_url')

// Cancelar um agendamento é destrutivo do ponto de vista do operador: confirm.
assert.match(
  script,
  /async function cancelarPostagem[\s\S]{0,700}window\.confirm/,
  'cancelar postagem pede confirm',
)
assert.match(
  script,
  /\/api\/marketing\/postagens\/\$\{p\.id\}`,\s*\{\s*method: 'DELETE'/,
  'cancelar = DELETE /api/marketing/postagens/{id}',
)
assert.match(script, /'\/api\/marketing\/postagens',\s*\{\s*method: 'POST'/, 'agendar = POST /api/marketing/postagens')
assert.match(script, /\/api\/marketing\/postagens\/contas\?\$\{q\.toString\(\)\}/, 'contas da marca')

// Agendamento: datetime-local (BRT) e escolha "agora" vs "agendar".
assert.match(tpl, /type="datetime-local"/, 'input de data/hora do agendamento')
assert.match(tpl, /type="radio" value="agora"/, 'opção publicar agora')
assert.match(tpl, /type="radio" value="agendar"/, 'opção agendar')

// Contador da legenda (2200) e trava no próprio input.
assert.match(tpl, /\{\{ pubLegenda\.length \}\} \/ \{\{ LEGENDA_MAX \}\}/, 'contador da legenda')
assert.match(tpl, /:maxlength="LEGENDA_MAX"/, 'textarea limitada ao mesmo máximo')

// Conta sem token fica DESABILITADA com o motivo ao lado (nunca some da lista:
// o operador precisa ver que a conta existe e por que não dá).
assert.match(tpl, /:disabled="!c\.pode_postar"/, 'checkbox da conta bloqueada')
assert.match(tpl, /motivoConta\(c\.motivo\)/, 'motivo traduzido ao lado da conta')

// Modo seco: aviso âmbar quando o servidor diz commit=false.
assert.match(tpl, /v-if="pubCommit === false"/, 'aviso de modo seco')
assert.match(tpl, /v-else-if="pubCommit === null"/, 'aviso discreto quando o servidor não informa')

// Botão publicar: desabilitado com o motivo no SPAN de fora (botão desabilitado
// não dispara tooltip no Chrome/Safari).
assert.match(tpl, /:title="motivoPublicar\(r, canEdit\) \|\| /, 'motivo no title')
assert.match(tpl, /:disabled="!!motivoPublicar\(r, canEdit\)"/, 'botão travado pelo mesmo motivo')

// Coluna nova entre Aprovado e as ações; estados vazios acompanham o colspan.
{
  const thead = tpl.slice(tpl.indexOf('<thead'), tpl.indexOf('</thead>'))
  const pos = ['>Aprovado<', '>Publicação<'].map((x) => thead.indexOf(x))
  assert.ok(pos.every((p) => p >= 0), `cabeçalhos presentes: ${pos}`)
  assert.ok(pos[0] < pos[1], 'Publicação vem depois de Aprovado')
  assert.equal((tpl.match(/colspan="9"/g) || []).length, 3, 'os 3 estados vazios cobrem 9 colunas')
  assert.ok(!/colspan="8"/.test(tpl), 'nenhum colspan velho de 8 sobrou')
}

// MARCA_OPTIONS hardcoded morreu — as marcas vêm do cadastro (com fallback).
assert.ok(!/MARCA_OPTIONS/.test(script + tpl), 'lista fixa de marcas removida')
assert.match(script, /api<Marca\[\]>\('\/api\/marcas\?ativo=true'\)/, 'marcas vêm de /api/marcas')
assert.match(script, /const MARCA_FALLBACK = \['uranyx', 'charlots'\]/, 'fallback preservado')
assert.match(tpl, /v-for="o in marcaOptions"/, 'selects usam as marcas carregadas')

// Trocar/subir arquivo numa linha com postagem agendada avisa antes.
assert.match(script, /temPostagemEmVoo\(r\) && !window\.confirm/, 'upload avisa sobre agendamento')

// ---------------------------------------------------------------- helpers puros
const start = script.indexOf('// ---------- helpers puros')
const end = script.indexOf('// ---------- fim helpers puros')
assert.ok(start > 0 && end > start, 'marcadores dos helpers puros presentes')
const helpersJs = transpile(script.slice(start, end), ts.ModuleKind.ESNext)
const H = new Function('isoToday', 'PLATAFORMA_LABELS', 'MARCAS_ERROS', helpersJs + `
return {
  LEGENDA_MAX, STATUS_EM_VOO, brtParaIso, proximaHoraBrt, fmtBrtCurto, fmtBrtLongo,
  contaLabel, plataformaLabel, motivoConta, motivoPublicar, montaBody, normalizaContas,
  statusLabel, statusPill, podeCancelar, emVoo, ordenaPostagens, postagemQuando, postagemTitle,
  POSTAGEM_ERR_MAP,
};
`)(dateLib.isoToday, redes.PLATAFORMA_LABELS, apiError.MARCAS_ERROS)

// Limite da legenda = 2200 (Instagram), igual ao que o template mostra.
assert.equal(H.LEGENDA_MAX, 2200)
// Espelha STATUS_EM_VOO de app/models/marketing_postagem.py.
assert.deepEqual(H.STATUS_EM_VOO, ['agendado', 'pendente', 'containering', 'publicando'])

// brtParaIso: o que o datetime-local entrega vira ISO com offset EXPLÍCITO.
{
  assert.equal(H.brtParaIso('2026-09-16T14:30'), '2026-09-16T14:30:00-03:00')
  assert.equal(H.brtParaIso(' 2026-09-16T14:30 '), '2026-09-16T14:30:00-03:00', 'tolera espaço')
  assert.equal(H.brtParaIso('2026-09-16T14:30:00'), '2026-09-16T14:30:00-03:00', 'browser com segundos')
  // O instante resultante é o esperado: 14:30 BRT = 17:30 UTC.
  assert.equal(new Date(H.brtParaIso('2026-09-16T14:30')).toISOString(), '2026-09-16T17:30:00.000Z')
  for (const ruim of ['', null, undefined, '2026-09-16', '16/09/2026 14:30', '2026-13-01T10:00', '2026-09-16T25:00', '2026-09-16T10:75']) {
    assert.equal(H.brtParaIso(ruim), null, `inválido: ${ruim}`)
  }
}

// proximaHoraBrt: hora cheia, pelo menos 1h à frente (o FB exige 10 min).
{
  assert.equal(H.proximaHoraBrt(new Date('2026-09-15T17:20:00Z')), '2026-09-15T16:00')
  assert.equal(H.proximaHoraBrt(new Date('2026-09-15T17:00:00Z')), '2026-09-15T15:00')
  // 01:00 UTC do dia 16 ainda é dia 15 em BRT — o drift de 3h que derrubaria
  // um agendamento "pra hoje" feito depois das 21h.
  assert.equal(H.proximaHoraBrt(new Date('2026-09-16T01:00:00Z')), '2026-09-15T23:00')
  const ida = H.brtParaIso(H.proximaHoraBrt(new Date('2026-09-15T17:20:00Z')))
  assert.equal(new Date(ida).toISOString(), '2026-09-15T19:00:00.000Z', 'volta pro mesmo instante')
}

// fmtBrtCurto / fmtBrtLongo: BRT sempre, hoje só com a hora.
{
  const iso = '2026-09-15T17:30:00+00:00' // 14:30 em BRT
  assert.equal(H.fmtBrtCurto(iso, '2026-09-15'), '14:30')
  assert.equal(H.fmtBrtCurto(iso, '2026-09-16'), '15/09 14:30')
  assert.equal(H.fmtBrtLongo(iso), '15/09/2026 14:30')
  // 01:00 UTC ainda é ontem às 22:00 em BRT — o drift de 3h que ~/lib/date evita.
  assert.equal(H.fmtBrtCurto('2026-09-16T01:00:00Z', '2026-09-15'), '22:00')
  assert.equal(H.fmtBrtCurto('2026-09-16T01:00:00Z', '2026-09-16'), '15/09 22:00')
  for (const vazio of [null, undefined, '', 'nada disso']) {
    assert.equal(H.fmtBrtCurto(vazio, '2026-09-15'), '')
    assert.equal(H.fmtBrtLongo(vazio), '')
  }
}

// contaLabel / plataformaLabel
{
  assert.equal(H.contaLabel({ conta: 'loja.teste' }), '@loja.teste')
  assert.equal(H.contaLabel({ conta: '@@loja.teste' }), '@loja.teste', 'não duplica o @')
  assert.equal(H.contaLabel({ conta: null }), '(conta removida)')
  assert.equal(H.plataformaLabel('instagram'), 'Instagram')
  assert.equal(H.plataformaLabel('orkut'), 'orkut', 'plataforma desconhecida não quebra')
}

// motivoConta: código do backend traduzido; frase pronta passa direto.
{
  assert.match(H.motivoConta('conta_sem_token'), /não conectada/)
  assert.match(H.motivoConta('conta_inativa'), /inativa/)
  assert.match(H.motivoConta('plataforma_nao_suportada'), /ainda não publica/)
  assert.match(H.motivoConta('token_expirado'), /expirado/)
  assert.equal(H.motivoConta('motivo que o backend escreveu'), 'motivo que o backend escreveu')
  assert.equal(H.motivoConta(null), 'indisponível')
  assert.equal(H.motivoConta('   '), 'indisponível')
  // Traduzido = pt-BR de verdade, sem vazar o código cru pro operador.
  for (const c of ['conta_sem_token', 'conta_inativa', 'plataforma_nao_suportada']) {
    assert.ok(!H.motivoConta(c).includes('_'), `sem código cru: ${c}`)
  }
}

// motivoPublicar: só aprovado + com arquivo + com permissão libera o botão.
{
  const linha = (over = {}) => ({
    id: 'c1', modelo: 'video 30s', marca: 'poofy', sku: 'SKU1', equipe: 'time', roteiro: 'cena 1',
    files: [{ id: 'f1', file_name: 'reel.mp4', file_mime: 'video/mp4', file_size: 10 }],
    aprovado: true, pushed_at: null, pushed_dest: null, created_at: null, ...over,
  })
  assert.equal(H.motivoPublicar(linha(), true), null)
  assert.match(H.motivoPublicar(linha(), false), /permissão/)
  assert.match(H.motivoPublicar(linha({ files: [] }), true), /Anexe o arquivo/)
  assert.match(H.motivoPublicar(linha({ aprovado: null }), true), /aprovado/)
  assert.match(H.motivoPublicar(linha({ aprovado: false }), true), /aprovado/)
}

// montaBody: o corpo do POST /api/marketing/postagens.
{
  const base = {
    creativeId: 'c1', fileId: 'f1', redeSocialIds: ['r1', 'r2'],
    legenda: '  legenda de teste  ', quando: 'agora', dataHora: '2026-09-16T14:30',
    shareToFeed: true, temInstagram: false,
  }
  const agora = H.montaBody(base)
  assert.equal(agora.creative_id, 'c1')
  assert.equal(agora.file_id, 'f1')
  assert.deepEqual(agora.rede_social_ids, ['r1', 'r2'])
  assert.notEqual(agora.rede_social_ids, base.redeSocialIds, 'copia a lista (não manda a reativa)')
  assert.equal(agora.legenda, 'legenda de teste', 'legenda com trim')
  assert.equal(agora.agendado_para, null, 'publicar agora = sem agendamento')
  assert.deepEqual(agora.opcoes, {}, 'sem Instagram, sem share_to_feed')

  const agendado = H.montaBody({ ...base, quando: 'agendar', temInstagram: true })
  assert.equal(agendado.agendado_para, '2026-09-16T14:30:00-03:00')
  assert.deepEqual(agendado.opcoes, { share_to_feed: true })
  assert.deepEqual(
    H.montaBody({ ...base, quando: 'agendar', temInstagram: true, shareToFeed: false }).opcoes,
    { share_to_feed: false },
  )
  // Legenda em branco vira null (coluna nullable), nunca string vazia.
  assert.equal(H.montaBody({ ...base, legenda: '   ' }).legenda, null)
  // Data inválida não é "inventada": vai null e o salvar trava antes disso.
  assert.equal(H.montaBody({ ...base, quando: 'agendar', dataHora: 'xx' }).agendado_para, null)
  // Nada de token/senha viajando no corpo.
  assert.deepEqual(
    Object.keys(agendado).sort(),
    ['agendado_para', 'creative_id', 'file_id', 'legenda', 'opcoes', 'rede_social_ids'],
  )
}

// normalizaContas: aceita lista pura OU envelope {commit, contas}.
{
  const crua = [
    { id: 'r1', plataforma: 'instagram', conta: 'poofy', pode_postar: true, motivo: null },
    { rede_social_id: 'r2', plataforma: 'facebook', conta: 'poofy.fb', pode_postar: false, motivo: 'conta_sem_token' },
    { plataforma: 'tiktok' },
  ]
  const a = H.normalizaContas(crua)
  assert.equal(a.commit, null, 'lista pura não diz nada sobre o modo seco')
  assert.equal(a.contas.length, 2, 'linha sem id é descartada')
  assert.equal(a.contas[1].id, 'r2', 'aceita rede_social_id como id')
  assert.equal(a.contas[1].pode_postar, false)

  const b = H.normalizaContas({ commit: false, contas: crua })
  assert.equal(b.commit, false, 'modo seco informado pelo servidor')
  assert.equal(b.contas.length, 2)
  assert.equal(H.normalizaContas({ commit: true, contas: [] }).commit, true)
  assert.equal(H.normalizaContas({ modo_seco: true, contas: [] }).commit, false, 'modo_seco é o inverso de commit')
  // Lixo não derruba a tela.
  for (const ruim of [null, undefined, {}, 'x', 42]) {
    assert.deepEqual(H.normalizaContas(ruim), { contas: [], commit: null })
  }
  // pode_postar só é verdade quando vem true de verdade (nada de "truthy").
  assert.equal(H.normalizaContas([{ id: 'r9', pode_postar: 'sim' }]).contas[0].pode_postar, false)
}

// status → pill/rótulo/cancelável
{
  assert.equal(H.statusPill('publicado'), 'pill-success')
  assert.equal(H.statusPill('agendado'), 'pill-warning')
  assert.equal(H.statusPill('publicando'), 'pill-warning')
  assert.equal(H.statusPill('falhou'), 'pill-danger')
  assert.equal(H.statusPill('revisar'), 'pill-danger')
  assert.equal(H.statusPill('cancelado'), 'pill-muted')
  assert.equal(H.statusPill('zzz'), 'pill-muted', 'status novo do backend não quebra a pill')
  assert.equal(H.statusLabel('pendente'), 'na fila')
  assert.equal(H.statusLabel('zzz'), 'zzz')
  assert.equal(H.emVoo('containering'), true)
  assert.equal(H.emVoo('cancelado'), false)
  // Publicar não tem desfazer: o que já está com a Meta não se cancela daqui.
  assert.equal(H.podeCancelar({ status: 'agendado' }), true)
  assert.equal(H.podeCancelar({ status: 'pendente' }), true)
  assert.equal(H.podeCancelar({ status: 'revisar' }), true)
  assert.equal(H.podeCancelar({ status: 'containering' }), false)
  assert.equal(H.podeCancelar({ status: 'publicando' }), false)
  assert.equal(H.podeCancelar({ status: 'publicado' }), false)
}

// postagemQuando / postagemTitle: data BRT e o erro no tooltip quando falhou.
{
  const post = (over = {}) => ({
    id: 'p1', creative_id: 'c1', file_id: 'f1', rede_social_id: 'r1',
    plataforma: 'instagram', conta: 'poofy', status: 'agendado',
    agendado_para: '2026-09-16T17:30:00+00:00', publicado_em: null,
    post_url: null, result: null, created_at: '2026-09-15T12:00:00+00:00', ...over,
  })
  assert.equal(H.postagemQuando(post(), '2026-09-15'), '16/09 14:30')
  assert.equal(
    H.postagemQuando(post({ status: 'publicado', publicado_em: '2026-09-16T17:35:00+00:00' }), '2026-09-16'),
    '14:35',
    'publicado mostra quando saiu',
  )

  const t = H.postagemTitle(post())
  assert.match(t, /^Instagram @poofy · agendado · agendado para 16\/09\/2026 14:30 \(BRT\)$/)
  const falhou = H.postagemTitle(post({ status: 'falhou', result: 'Meta recusou: vídeo curto demais' }))
  assert.match(falhou, /falhou/)
  assert.match(falhou, /vídeo curto demais/, 'o erro do robô aparece no tooltip')
  const publicado = H.postagemTitle(post({
    status: 'publicado',
    publicado_em: '2026-09-16T17:35:00+00:00',
    post_url: 'https://www.instagram.com/reel/FAKE123/',
  }))
  assert.match(publicado, /publicado em 16\/09\/2026 14:35/)
  assert.match(publicado, /instagram\.com\/reel\/FAKE123/)
  assert.match(H.postagemTitle(post({ status: 'revisar' })), /decida se ainda vale/)
  assert.match(H.postagemTitle(post({ status: 'pendente', agendado_para: null })), /próximo ciclo/)
  assert.match(H.postagemTitle(post({ conta: null })), /\(conta removida\)/, 'conta apagada não vira "@null"')

  // Ordem: em voo primeiro (é o que dá pra cancelar), depois publicado, resto no fim.
  const lista = [
    post({ id: 'velho', status: 'cancelado' }),
    post({ id: 'ok', status: 'publicado', publicado_em: '2026-09-14T12:00:00Z' }),
    post({ id: 'agenda', status: 'agendado' }),
  ]
  assert.deepEqual(H.ordenaPostagens(lista).map((p) => p.id), ['agenda', 'ok', 'velho'])
  assert.deepEqual(lista.map((p) => p.id), ['velho', 'ok', 'agenda'], 'ordena sem mexer no array original')
}

// Erros do robô traduzidos em pt-BR por cima do MARCAS_ERROS (apiErrMsg).
{
  const novos = [
    'criativo_nao_aprovado', 'postagem_em_voo', 'video_ja_usado_em_outra_marca', 'conta_sem_token',
    'conta_inativa', 'plataforma_nao_suportada', 'limite_diario', 'intervalo_curto', 'arquivo_sumiu',
  ]
  for (const code of novos) {
    const msg = apiError.apiErrMsg({ data: { detail: { code } } }, H.POSTAGEM_ERR_MAP)
    assert.ok(msg && msg !== code, `código traduzido: ${code}`)
    assert.ok(!msg.includes('_'), `sem código cru na tela: ${code}`)
  }
  // Os códigos antigos de Marcas continuam valendo (o mapa só acrescenta).
  assert.equal(
    apiError.apiErrMsg({ data: { detail: { code: 'forbidden' } } }, H.POSTAGEM_ERR_MAP),
    'Sem permissão',
  )
  // 422 do Pydantic continua legível.
  assert.match(
    apiError.apiErrMsg({ data: { detail: [{ loc: ['body', 'legenda'], msg: 'muito longa' }] } }, H.POSTAGEM_ERR_MAP),
    /legenda: muito longa/,
  )
}

// ---------------------------------------------------------------- script setup
// Molde de marcas-sfc.cjs / redes-sociais-sfc.cjs: executa o <script setup>
// com api, window e timers FALSOS pra travar o fluxo do modal de ponta a ponta
// (o que sai no corpo do POST, o que trava, o que pede confirm). Nenhuma rede.
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  rows, marcas, marcaOptions, marcaValues, postagens, postagensDe, temPostagemEmVoo,
  pub, pubFiles, pubFileId, pubMarcaId, pubMarcaNome, pubContas, pubSel, pubLegenda,
  pubQuando, pubDataHora, pubShareToFeed, pubCommit, pubErr, pubTemInstagram,
  openPublicar, closePublicar, toggleConta, salvarPostagem, cancelarPostagem, loadPostagens,
  motivoPublicar, pickFile,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'nextTick', 'onMounted', 'onBeforeUnmount', 'reactive',
  'useApi', 'useToasts', 'useAuthStore', 'useCan',
  'apiErrMsg', 'MARCAS_ERROS', 'isoToday', 'PLATAFORMA_LABELS', 'window', 'setTimeout',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

// Formato de ContaParaPostarOut (app/schemas/marketing_postagens.py): a chave
// é `rede_social_id`, e token nenhum vem junto — só `has_token`.
const CONTAS_FAKE = [
  { rede_social_id: 'r1', plataforma: 'instagram', conta: 'poofy', ativo: true, has_token: true, pode_postar: true, motivo: null },
  { rede_social_id: 'r2', plataforma: 'facebook', conta: 'poofy.fb', ativo: true, has_token: false, pode_postar: false, motivo: 'conta_sem_token' },
]

function criativo(over = {}) {
  return {
    id: 'c1', modelo: 'video 30s', marca: 'poofy', marca_id: null, sku: 'SKU1', equipe: 'time',
    roteiro: 'cena 1: mostrar o produto na mão',
    files: [
      { id: 'fimg', file_name: 'capa.png', file_mime: 'image/png', file_size: 10 },
      { id: 'fvid', file_name: 'reel.mp4', file_mime: 'video/mp4', file_size: 20 },
    ],
    aprovado: true, pushed_at: null, pushed_dest: null, created_at: null, ...over,
  }
}

async function tela({
  canEdit = true, linhas = [criativo()], contasResp = { commit: false, contas: CONTAS_FAKE },
  marcasErro = false, postagensIniciais = [], postError = null, confirmAnswer = true,
} = {}) {
  const calls = []
  const toastLog = []
  const confirms = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marketing/creatives') return Promise.resolve(linhas.map((l) => ({ ...l })))
    if (url === '/api/marketing/creatives/equipes') return Promise.resolve(['time'])
    if (url === '/api/marcas?ativo=true') {
      return marcasErro
        ? Promise.reject(new Error('sem permissão'))
        : Promise.resolve([{ id: 'm1', nome: 'Poofy', slug: 'poofy', ativo: true }])
    }
    if (url === '/api/marketing/postagens' && !opts?.method) {
      // 'erro' simula o endpoint que ainda não subiu no backend.
      return postagensIniciais === 'erro'
        ? Promise.reject(new Error('404'))
        : Promise.resolve(postagensIniciais)
    }
    if (url.startsWith('/api/marketing/postagens/contas')) return Promise.resolve(contasResp)
    if (opts?.method === 'POST' && url === '/api/marketing/postagens') {
      return postError ? Promise.reject(postError) : Promise.resolve({})
    }
    if (opts?.method === 'DELETE') return Promise.resolve(null)
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const toasts = {
    success: (...a) => toastLog.push(['success', ...a]),
    error: (...a) => toastLog.push(['error', ...a]),
    warning: (...a) => toastLog.push(['warning', ...a]),
    info: (...a) => toastLog.push(['info', ...a]),
    push: () => 1,
    dismiss: () => {},
  }
  const state = await factory(
    Vue.ref, Vue.computed, Vue.nextTick,
    // onMounted roda na hora aqui — por isso a carga da agenda tem o hook
    // dela junto da própria seção, sem depender da ordem de declaração.
    (fn) => fn(), () => {}, Vue.reactive,
    () => ({ api }), () => toasts, () => ({ user: { role: 'admin' } }),
    () => Vue.ref(canEdit),
    apiError.apiErrMsg, apiError.MARCAS_ERROS, dateLib.isoToday, redes.PLATAFORMA_LABELS,
    { confirm: (m) => { confirms.push(m); return confirmAnswer }, addEventListener: () => {}, removeEventListener: () => {} },
    (fn) => fn,
  )
  await new Promise(setImmediate)
  return { s: state, calls, toastLog, confirms }
}

const posts = (calls) => calls.filter((c) => c.opts?.method === 'POST')

async function run() {
  // Carga inicial: criativos, equipes, marcas e a agenda de postagens.
  {
    const { s, calls } = await tela()
    const urls = calls.map((c) => c.url)
    assert.ok(urls.includes('/api/marcas?ativo=true'), 'busca as marcas do cadastro')
    assert.ok(urls.includes('/api/marketing/postagens'), 'busca a agenda de postagens')
    assert.deepEqual(s.marcaOptions.value, [{ value: 'poofy', label: 'Poofy' }])
    assert.deepEqual(s.marcaValues.value, ['poofy'])
  }
  // /api/marcas fora do ar não pode travar a edição das linhas antigas.
  {
    const { s } = await tela({ marcasErro: true })
    assert.deepEqual(s.marcaValues.value, ['uranyx', 'charlots'], 'cai no fallback')
  }

  // Abrir o modal: marca casada pelo slug, vídeo escolhido na frente da
  // imagem, legenda vinda do roteiro, conta bloqueada não entra na seleção.
  {
    const { s, calls } = await tela()
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    assert.ok(s.pub.value, 'modal abriu')
    assert.equal(s.pubMarcaId.value, 'm1', 'marca casada pelo slug da coluna')
    assert.equal(s.pubMarcaNome.value, 'Poofy')
    assert.equal(s.pubFileId.value, 'fvid', 'vídeo na frente da imagem (o robô publica Reels)')
    assert.equal(s.pubLegenda.value, 'cena 1: mostrar o produto na mão', 'roteiro entra como rascunho')
    assert.equal(s.pubQuando.value, 'agora')
    assert.match(s.pubDataHora.value, /^\d{4}-\d{2}-\d{2}T\d{2}:00$/, 'data/hora default é hora cheia')
    assert.equal(s.pubCommit.value, false, 'modo seco veio do servidor')
    assert.deepEqual(s.pubSel.value, ['r1'], 'única conta liberada já vem marcada')
    assert.equal(s.pubTemInstagram.value, true)
    // creative_id + file_id vão junto: é o que faz o backend responder com o
    // mesmo veredito do publicador (teto do dia, vídeo repetido, em voo).
    const contasUrl = calls.at(-1).url
    assert.ok(contasUrl.startsWith('/api/marketing/postagens/contas?'), contasUrl)
    assert.match(contasUrl, /marca_id=m1/)
    assert.match(contasUrl, /creative_id=c1/)
    assert.match(contasUrl, /file_id=fvid/)
    // Conta sem token não entra na seleção nem clicando.
    s.toggleConta(s.pubContas.value[1])
    assert.deepEqual(s.pubSel.value, ['r1'], 'conta bloqueada não pode ser marcada')
    s.toggleConta(s.pubContas.value[0])
    assert.deepEqual(s.pubSel.value, [], 'desmarcar funciona')
  }

  // Com duas contas liberadas nada vem marcado — publicar em tudo por engano
  // não tem desfazer.
  {
    const { s } = await tela({
      contasResp: { commit: true, contas: CONTAS_FAKE.map((c) => ({ ...c, pode_postar: true, motivo: null })) },
    // (envelope com commit — o /contas de hoje devolve lista pura; ver notes)
    })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    assert.deepEqual(s.pubSel.value, [])
    assert.equal(s.pubCommit.value, true)
  }

  // Agendar: POST com o corpo certo, toast, modal fecha e a agenda recarrega.
  {
    const { s, calls, toastLog } = await tela()
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    s.pubQuando.value = 'agendar'
    s.pubDataHora.value = '2026-09-16T14:30'
    s.pubLegenda.value = '  vem ver esse produto  '
    await s.salvarPostagem()
    const post = posts(calls).at(-1)
    assert.equal(post.url, '/api/marketing/postagens')
    assert.deepEqual(post.opts.body, {
      creative_id: 'c1',
      file_id: 'fvid',
      rede_social_ids: ['r1'],
      legenda: 'vem ver esse produto',
      agendado_para: '2026-09-16T14:30:00-03:00',
      opcoes: { share_to_feed: true },
    })
    assert.equal(toastLog.at(-1)[1], 'Postagem agendada')
    assert.equal(s.pub.value, null, 'modal fecha')
    assert.equal(calls.at(-1).url, '/api/marketing/postagens', 'recarrega a agenda')
  }

  // Travas do salvar: sem conta marcada e data inválida não chegam a postar.
  {
    const { s, calls } = await tela()
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    s.pubSel.value = []
    await s.salvarPostagem()
    assert.match(s.pubErr.value, /pelo menos uma conta/)
    assert.equal(posts(calls).length, 0)
    s.pubSel.value = ['r1']
    s.pubQuando.value = 'agendar'
    s.pubDataHora.value = ''
    await s.salvarPostagem()
    assert.match(s.pubErr.value, /Data e hora/)
    assert.equal(posts(calls).length, 0, 'nada de agendamento sem hora válida')
  }

  // Erro da API vira pt-BR e o modal fica aberto com o que o operador digitou.
  {
    const { s } = await tela({ postError: { data: { detail: { code: 'postagem_em_voo' } } } })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    await s.salvarPostagem()
    assert.match(s.pubErr.value, /esperando pra sair/)
    assert.ok(s.pub.value, 'modal continua aberto')
  }

  // Linha não aprovada / sem arquivo / sem permissão: o modal nem abre.
  {
    for (const [over, esperado] of [
      [{ aprovado: null }, /aprovado/],
      [{ files: [] }, /Anexe o arquivo/],
    ]) {
      const { s, toastLog } = await tela({ linhas: [criativo(over)] })
      s.openPublicar(s.rows.value[0])
      assert.equal(s.pub.value, null)
      assert.match(toastLog.at(-1)[2], esperado)
    }
    const { s: sv, toastLog: tv } = await tela({ canEdit: false })
    assert.match(sv.motivoPublicar(sv.rows.value[0], false), /permissão/)
    sv.openPublicar(sv.rows.value[0])
    assert.equal(sv.pub.value, null)
    assert.match(tv.at(-1)[2], /permissão/)
  }

  // Cancelar: confirm obrigatório, DELETE e recarga; recusar não manda nada.
  {
    const agendada = {
      id: 'p1', creative_id: 'c1', file_id: 'fvid', rede_social_id: 'r1', plataforma: 'instagram',
      conta: 'poofy', status: 'agendado', agendado_para: '2026-09-16T17:30:00+00:00',
      publicado_em: null, post_url: null, result: null, created_at: '2026-09-15T12:00:00+00:00',
    }
    const { s, calls, confirms } = await tela({ postagensIniciais: [agendada] })
    assert.equal(s.postagensDe(s.rows.value[0]).length, 1)
    assert.equal(s.temPostagemEmVoo(s.rows.value[0]), true)
    await s.cancelarPostagem(agendada)
    assert.equal(confirms.length, 1)
    assert.match(confirms[0], /@poofy/)
    assert.ok(!/senha|token/i.test(confirms[0]), 'confirm nunca fala de credencial')
    assert.equal(calls.find((c) => c.opts?.method === 'DELETE').url, '/api/marketing/postagens/p1')

    // Subir arquivo novo numa linha com agendamento avisa antes (o upload
    // zera `aprovado` e o robô não publica sem aprovação).
    const { s: s2, confirms: c2 } = await tela({ postagensIniciais: [agendada], confirmAnswer: false })
    s2.pickFile(s2.rows.value[0])
    assert.equal(c2.length, 1)
    assert.match(c2[0], /postagem agendada/)

    const { s: s3, calls: c3, confirms: cf3 } = await tela({ postagensIniciais: [agendada], confirmAnswer: false })
    await s3.cancelarPostagem(agendada)
    assert.equal(cf3.length, 1)
    assert.equal(c3.filter((c) => c.opts?.method === 'DELETE').length, 0, 'confirm recusado não apaga nada')

    // Postagem cujo vídeo JÁ subiu pra Meta: cancelar libera um novo
    // agendamento do mesmo vídeo, então o aviso tem que mandar conferir a
    // conta primeiro — é o que separa "refazer" de "postar duas vezes".
    const duvidosa = { ...agendada, id: 'p9', status: 'revisar', tem_container: true }
    const { s: s4, confirms: cf4 } = await tela({ postagensIniciais: [duvidosa] })
    await s4.cancelarPostagem(duvidosa)
    assert.match(cf4[0], /confira se o post saiu/i, 'cancelar com container manda conferir')
    assert.ok(!/senha|token/i.test(cf4[0]), 'confirm nunca fala de credencial')
  }

  // Backend ainda sem o endpoint (ou sem permissão): a tela não quebra.
  {
    const { s } = await tela({ postagensIniciais: 'erro' })
    assert.deepEqual(s.postagens.value, [], 'agenda vazia em vez de erro na cara do operador')
  }
}

run().then(() => {
  console.log('PASS: SFC parse + template compile; higiene (sem token/senha, rel=noopener, confirm no cancelar); helpers puros (BRT→ISO, corpo do POST, contas, pills, motivos); script setup com api falso (modal, travas, cancelar)')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
