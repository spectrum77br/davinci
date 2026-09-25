// Run from apps/web: node tests/marketing-criativos-sfc.cjs
// Marketing › Criativos — robô de postagem (Eduardo, 15/09/2026): parse +
// compileTemplate do componente (molde de redes-sociais-sfc.cjs), higiene
// estática do template (nenhum token/senha passa por aqui; link do post com
// rel=noopener; cancelar pede confirm; datetime-local presente; contador da
// legenda) e os helpers PUROS do modal — montagem do corpo do POST, conversão
// BRT → ISO com offset explícito e tradução dos motivos de uma conta não
// poder receber post. Só dados FALSOS aqui; nenhuma chamada de rede.
//
// Legenda automática (Eduardo, 16/09/2026): o modal parou de pré-preencher com
// `roteiro` (prompt de geração de vídeo, em inglês) e passou a pedir a legenda
// JÁ RENDERIZADA ao backend. O que está travado aqui é o que custa caro se
// quebrar: roteiro nunca mais vira legenda, o texto mostrado é o que vai no
// POST, endpoint fora do ar não derruba a tela, e post sem legenda pede
// confirmação — Reel mudo é criativo queimado.
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

// Rótulo da legenda (origem · variação · 412/2200) e trava no próprio input.
assert.match(tpl, /\{\{ pubLegendaRotulo \}\}/, 'rótulo com origem e contador da legenda')
assert.match(tpl, /:maxlength="LEGENDA_MAX"/, 'textarea limitada ao mesmo máximo')
assert.ok(!/\{\{ pubLegenda\.length \}\}/.test(tpl), 'um contador só na caixa (o do rótulo)')

// A legenda vem do backend JÁ RENDERIZADA; o roteiro (prompt de geração do
// vídeo, em inglês) nunca mais entra no textarea. Esta é a razão de existir da
// mudança inteira — se voltar, volta escondido numa linha dessas.
assert.ok(
  !/pubLegenda\.value = \(?r\.roteiro/.test(script),
  'legenda não é mais pré-preenchida com o roteiro',
)
{
  const caixa = tpl.indexOf('<!-- legenda -->')
  assert.ok(caixa > 0, 'marcador da caixa da legenda presente')
  assert.ok(!/roteiro/.test(tpl.slice(caixa)), 'roteiro não aparece na caixa da legenda')
}
assert.match(
  script,
  /\/api\/marketing\/legendas\/resolvida\?\$\{q\.toString\(\)\}/,
  'legenda resolvida vem do backend',
)
// Sem v-model: é o @input que grava o texto E marca a origem como manual.
assert.match(tpl, /:value="pubLegenda"/, 'textarea controlada pelo estado')
assert.match(tpl, /@input="onLegendaInput"/, 'digitar marca a legenda como manual')
assert.ok(!/v-model="pubLegenda"/.test(tpl), 'v-model trocado pelo handler explícito')

// Post sem legenda: aviso âmbar na tela e confirm no publicar (mesma régua do
// cancelar — o que não tem desfazer pergunta antes).
assert.match(tpl, /v-if="pubSemLegenda && !pubLegendaLoading"/, 'aviso âmbar de post sem legenda')
assert.match(
  script,
  /async function salvarPostagem[\s\S]{0,1600}pubSemLegenda\.value && !window\.confirm/,
  'publicar sem legenda pede confirm',
)

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
  // 3 estados vazios + o cabeçalho de cada dia (Eduardo, 25/09/2026).
  assert.equal((tpl.match(/colspan="9"/g) || []).length, 4, 'estados vazios e cabeçalho do dia cobrem 9 colunas')
  assert.ok(!/colspan="8"/.test(tpl), 'nenhum colspan velho de 8 sobrou')
}

// MARCA_OPTIONS hardcoded morreu — as marcas vêm do cadastro (com fallback).
assert.ok(!/MARCA_OPTIONS/.test(script + tpl), 'lista fixa de marcas removida')
assert.match(script, /api<Marca\[\]>\('\/api\/marcas\?ativo=true'\)/, 'marcas vêm de /api/marcas')
assert.match(script, /const MARCA_FALLBACK = \['uranyx', 'charlots'\]/, 'fallback preservado')
assert.match(tpl, /v-for="o in marcaOptions"/, 'selects usam as marcas carregadas')

// Trocar/subir arquivo numa linha com postagem agendada avisa antes.
assert.match(script, /temPostagemEmVoo\(r\) && !window\.confirm/, 'upload avisa sobre agendamento')

// A legenda resolvida é de UMA conta (o {{ instagram }} é o @ dela). Mandá-la
// de volta no POST faria o arroba da primeira ir pras outras e congelaria o
// rodízio numa variação só — o backend resolve por conta no agendar().
assert.match(
  script,
  /legendaOrigem === 'manual' \? \(o\.legenda \|\| ''\)\.trim\(\) : ''/,
  'legenda só viaja quando foi reescrita à mão',
)

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
  chaveConta, escondeFalhasSuperadas,
  POSTAGEM_ERR_MAP, legendaRotulo, normalizaLegenda, LEGENDA_ORIGEM_LABEL,
  diaBrt, diaRotulo, noIntervalo, agrupaPorDia, grupoResumo, agrupaFila, moveItem,
  filaIdsAposMover, FILA_ERR_MAP, diaDaLinha, bloqueioRotulo,
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
    legenda: '  legenda de teste  ', legendaOrigem: 'manual',
    quando: 'agora', dataHora: '2026-09-16T14:30',
    shareToFeed: true, temInstagram: false,
  }
  const agora = H.montaBody(base)
  assert.equal(agora.creative_id, 'c1')
  assert.equal(agora.file_id, 'f1')
  assert.deepEqual(agora.rede_social_ids, ['r1', 'r2'])
  assert.notEqual(agora.rede_social_ids, base.redeSocialIds, 'copia a lista (não manda a reativa)')
  assert.equal(agora.legenda, 'legenda de teste', 'legenda com trim')
  // Legenda RESOLVIDA (não reescrita) não volta no corpo: ela é de uma conta
  // só, e o backend resolve por conta dentro do agendar().
  for (const origem of ['marca', 'produto', 'criativo', 'nenhuma']) {
    assert.equal(
      H.montaBody({ ...base, legendaOrigem: origem }).legenda, null,
      `origem ${origem} não manda a legenda de volta`,
    )
  }
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

// legendaRotulo: "padrão da marca · variação 2 de 4 · 412/2200".
{
  const r = (o) => H.legendaRotulo({ origem: 'marca', total: 4, indice: 2, tamanho: 412, ...o })
  assert.equal(r(), 'padrão da marca · variação 2 de 4 · 412/2200')
  assert.equal(r({ origem: 'manual', total: 0, indice: 0 }), 'escrita agora · 412/2200')
  assert.equal(r({ origem: 'criativo', total: 0, indice: 0 }), 'legenda deste vídeo · 412/2200')
  assert.equal(r({ origem: 'produto', total: 0, indice: 0 }), 'legenda do produto · 412/2200')
  assert.equal(r({ origem: 'nenhuma', total: 0, indice: 0, tamanho: 0 }), 'sem legenda · 0/2200')
  // Variação só quando há rodízio de verdade: "variação 1 de 1" é ruído.
  assert.equal(r({ total: 1, indice: 1 }), 'padrão da marca · 412/2200')
  assert.equal(r({ total: 4, indice: 0 }), 'padrão da marca · 412/2200')
  // Origem que a tela não conhece passa crua (mesma regra do statusLabel) e
  // nunca some o contador — é ele que diz se estourou o limite.
  assert.equal(r({ origem: 'campanha', total: 0, indice: 0 }), 'campanha · 412/2200')
  assert.equal(r({ origem: '', total: 0, indice: 0 }), '412/2200')
  assert.match(r({ tamanho: 2400 }), /2400\/2200$/, 'estouro aparece no mesmo rótulo')
  // Todas as origens do contrato viram pt-BR, sem código cru na cara do operador.
  for (const o of ['manual', 'criativo', 'produto', 'marca', 'nenhuma']) {
    assert.ok(H.LEGENDA_ORIGEM_LABEL[o], `origem traduzida: ${o}`)
    assert.ok(!H.LEGENDA_ORIGEM_LABEL[o].includes('_'), `sem código cru: ${o}`)
  }
}

// normalizaLegenda: a resposta do endpoint, limpa. Texto vazio é SEMPRE
// "nenhuma" — é o texto que vai (ou não) pro Instagram, não a origem.
{
  const ok = H.normalizaLegenda({ texto: 'Conheça a Poofy', origem: 'marca', total_variacoes: 4, indice: 2 })
  assert.deepEqual(ok, { texto: 'Conheça a Poofy', origem: 'marca', total: 4, indice: 2 })
  assert.equal(H.normalizaLegenda({ texto: '', origem: 'marca' }).origem, 'nenhuma', 'texto vazio = sem legenda')
  assert.equal(H.normalizaLegenda({ texto: '   ', origem: 'produto' }).origem, 'nenhuma', 'só espaço também')
  assert.equal(H.normalizaLegenda({ texto: 'x' }).origem, '', 'origem ausente não vira mentira')
  // Índice é 1-based; fora da faixa não vira "variação 0 de 4" na tela.
  assert.equal(H.normalizaLegenda({ texto: 'x', total_variacoes: 4, indice: 0 }).indice, 0)
  assert.equal(H.normalizaLegenda({ texto: 'x', total_variacoes: 4, indice: 9 }).indice, 0)
  assert.equal(H.normalizaLegenda({ texto: 'x', total_variacoes: 4, indice: 4 }).indice, 4)
  assert.equal(H.normalizaLegenda({ texto: 'x', total_variacoes: -2 }).total, 0)
  assert.equal(H.normalizaLegenda({ texto: 'x', total_variacoes: '4' }).total, 0, 'número em string não conta')
  // Texto maior que o limite do Instagram é cortado ANTES de aparecer: mostrar
  // o que não caberia no post seria mentir pro operador.
  assert.equal(H.normalizaLegenda({ texto: 'a'.repeat(3000), origem: 'marca' }).texto.length, H.LEGENDA_MAX)
  // Lixo não derruba o modal.
  for (const ruim of [null, undefined, {}, 'x', 42, { texto: 123 }]) {
    assert.deepEqual(H.normalizaLegenda(ruim), { texto: '', origem: 'nenhuma', total: 0, indice: 0 })
  }
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

  // Falhas já superadas somem da linha (pedido do Eduardo, 23/09/2026): duas
  // tentativas vermelhas ao lado do post que deu certo não ajudam ninguém.
  {
    const naConta = (o) => post({ plataforma: 'tiktok', conta: 'poofy_brasil', ...o })
    const hist = [
      naConta({ id: 'f1', status: 'falhou', created_at: '2026-09-23T11:12:00Z' }),
      naConta({ id: 'f2', status: 'falhou', created_at: '2026-09-23T11:37:00Z' }),
      naConta({ id: 'ok', status: 'publicado', publicado_em: '2026-09-23T11:50:00Z',
                created_at: '2026-09-23T11:45:00Z' }),
    ]
    const vis = H.escondeFalhasSuperadas(hist)
    assert.deepEqual(vis.map((p) => p.id), ['ok'], 'as duas falhas anteriores saem da tela')
    assert.equal(vis[0].falhas_antes, 2, 'o sucesso carrega quantas falharam antes')
    assert.match(H.postagemTitle(vis[0]), /2 tentativas falharam antes desta/)
    // A lista original não pode ser mexida: alimenta o resto da tela.
    assert.deepEqual(hist.map((p) => p.id), ['f1', 'f2', 'ok'])
    assert.equal(hist[2].falhas_antes, undefined)

    // Falha DEPOIS do sucesso é notícia nova e continua aparecendo.
    const depois = H.escondeFalhasSuperadas([
      ...hist,
      naConta({ id: 'f3', status: 'falhou', created_at: '2026-09-23T12:10:00Z' }),
    ])
    assert.deepEqual(depois.map((p) => p.id).sort(), ['f3', 'ok'])

    // Sucesso numa conta NÃO limpa a falha de outra.
    const outra = H.escondeFalhasSuperadas([
      ...hist,
      post({ id: 'ig', plataforma: 'instagram', conta: 'charlots_br', status: 'falhou',
             created_at: '2026-09-23T11:00:00Z' }),
    ])
    assert.ok(outra.some((p) => p.id === 'ig'), 'falha de outra conta fica')

    // Sem nenhum sucesso, nada some.
    const soFalhas = [naConta({ id: 'x', status: 'falhou' })]
    assert.deepEqual(H.escondeFalhasSuperadas(soFalhas).map((p) => p.id), ['x'])
  }
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

// ---------------------------------------------------------------- fila do robô + filtro por dia
// Pedido do Eduardo (25/09/2026): escolher a ORDEM em que o robô posta
// (arrastar um vídeo antigo pra frente) sem perder a contagem de vídeos por
// dia. O que custa caro se quebrar: o PUT mandar ids a mais (congela a ordem
// por data de quem não foi mexido) ou a menos (o vídeo movido volta pro lugar),
// e o dia do cabeçalho sair em UTC (22h BRT já é "amanhã").
assert.match(tpl, /<option value="fila">fila do robô<\/option>/, 'opção "fila do robô" no select de status')
assert.equal((tpl.match(/type="date"/g) || []).length, 2, 'filtro "de" e "até"')
assert.match(tpl, /limpar datas/, 'link pra limpar as datas')
assert.match(tpl, /:draggable="canEdit && !filaSaving"/, 'arrastar só com permissão e fora de um PUT')
assert.match(tpl, /@drop\.prevent="onFilaDrop\(i\)"/, 'soltar reordena')
assert.match(tpl, /moverFila\(i, i - 1\)/, 'botão ↑ (teclado/celular)')
assert.match(tpl, /moverFila\(i, i \+ 1\)/, 'botão ↓ (teclado/celular)')
assert.match(tpl, /#\{\{ r\.fila_posicao \}\} na fila/, 'selo da posição na planilha')
assert.match(tpl, /⤒ frente da fila/, 'botão de furar a fila')
assert.match(script, /'\/api\/marketing\/creatives\/fila',\s*\{\s*method: 'PUT'/, 'reordenar = PUT /fila')
assert.match(script, /\/api\/marketing\/creatives\/fila\?\$\{q\.toString\(\)\}/, 'fila vem de GET /fila?marca_id=')
{
  // Nenhuma dependência nova de drag-and-drop: só o HTML5 nativo.
  const imports = [...script.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1])
  assert.deepEqual(
    [...new Set(imports)].sort(),
    ['lucide-vue-next', 'vue', '~/lib/apiError', '~/lib/date', '~/lib/redesSociais'],
    'sem lib nova',
  )
}

// diaBrt: o dia do operador. 22h30 BRT do dia 25 é 01h30 UTC do dia 26.
assert.equal(H.diaBrt('2026-09-26T01:30:00Z'), '2026-09-25')
assert.equal(H.diaBrt('2026-09-26T03:00:00Z'), '2026-09-26', 'meia-noite BRT já é o dia seguinte')
assert.equal(H.diaBrt(null), '')
assert.equal(H.diaBrt('lixo'), '')

// diaRotulo: dia da semana + dd/mm; o ano só quando não é o corrente.
assert.equal(H.diaRotulo('2026-09-25', '2026-09-25'), 'sex, 25/09')
assert.equal(H.diaRotulo('2026-01-01', '2026-09-25'), 'qui, 01/01')
assert.equal(H.diaRotulo('2025-12-31', '2026-01-02'), 'qua, 31/12/2025', 'outro ano mostra o ano')
assert.equal(H.diaRotulo(''), 'sem data')

// noIntervalo: as duas pontas inclusivas; linha sem data só passa sem filtro.
{
  const iso = '2026-09-26T01:30:00Z' // 25/09 em BRT
  assert.equal(H.noIntervalo(iso, '', ''), true)
  assert.equal(H.noIntervalo(iso, '2026-09-25', '2026-09-25'), true, 'um dia só, inclusivo')
  assert.equal(H.noIntervalo(iso, '2026-09-26', ''), false, 'o UTC (26) não vale — o dia é 25 em BRT')
  assert.equal(H.noIntervalo(iso, '', '2026-09-24'), false)
  assert.equal(H.noIntervalo(null, '', ''), true, 'sem filtro, sem data também aparece')
  assert.equal(H.noIntervalo(null, '2026-09-01', ''), false, 'com filtro, sem data fica de fora')
}

// agrupaPorDia: ordem de entrada, um cabeçalho por dia, vídeo = ARQUIVO de vídeo.
{
  const vid = { file_mime: 'video/mp4' }
  const img = { file_mime: 'image/png' }
  const lista = [
    { id: 'a', created_at: '2026-09-25T13:00:00Z', files: [vid, img] },
    { id: 'b', created_at: '2026-09-26T01:30:00Z', files: [vid, vid] }, // ainda 25/09 BRT
    { id: 'c', created_at: '2026-09-26T12:00:00Z', files: [] },
    { id: 'd', created_at: null, files: [vid] },
  ]
  const g = H.agrupaPorDia(lista)
  assert.deepEqual(g.map((x) => x.dia), ['2026-09-25', '2026-09-26', ''])
  assert.deepEqual(g[0].rows.map((r) => r.id), ['a', 'b'], 'mantém a ordem da API')
  assert.equal(g[0].criativos, 2)
  assert.equal(g[0].videos, 3, 'conta arquivo de vídeo, não imagem')
  assert.equal(H.grupoResumo(g[0], '2026-09-26'), 'sex, 25/09 · 2 criativos · 3 vídeos')
  assert.equal(H.grupoResumo(g[1], '2026-09-26'), 'sáb, 26/09 · 1 criativo · 0 vídeos')
  assert.equal(H.grupoResumo(g[2], '2026-09-26'), 'sem data · 1 criativo · 1 vídeo')
  assert.deepEqual(H.agrupaPorDia([]), [])
}

// diaDaLinha: o dia é o da CHEGADA DO VÍDEO (enviado_em), não o da linha. A
// linha nasce quando a entrega é aberta; o vídeo pode chegar dias depois.
{
  const lin = (created_at, files) => ({ created_at, files })
  const v = (enviado_em) => ({ file_mime: 'video/mp4', enviado_em })
  assert.equal(H.diaDaLinha(lin('2026-09-25T13:00:00Z', [v('2026-09-27T13:00:00Z')])), '2026-09-27')
  assert.equal(
    H.diaDaLinha(lin('2026-09-25T13:00:00Z', [v('2026-09-25T14:00:00Z'), v('2026-09-28T02:00:00Z')])),
    '2026-09-27', 'o vídeo mais novo manda, em BRT (02h UTC do 28 = 23h do 27)',
  )
  assert.equal(
    H.diaDaLinha(lin('2026-09-25T13:00:00Z', [{ file_mime: 'image/png', enviado_em: '2026-09-29T13:00:00Z' }])),
    '2026-09-25', 'imagem não move a linha',
  )
  assert.equal(H.diaDaLinha(lin('2026-09-25T13:00:00Z', [v(null), { file_mime: 'video/mp4' }])), '2026-09-25', 'sem enviado_em, a data da linha')
  assert.equal(H.diaDaLinha(lin(null, [])), '')

  // No agrupamento: a linha de 25 cujo vídeo chegou dia 27 conta no 27, e os
  // dias continuam em ordem mesmo ela vindo da API antes das linhas do 26.
  const g = H.agrupaPorDia([
    { id: 'a', ...lin('2026-09-25T13:00:00Z', [v('2026-09-27T13:00:00Z')]) },
    { id: 'b', ...lin('2026-09-26T13:00:00Z', [v('2026-09-26T13:00:00Z')]) },
    { id: 'c', ...lin(null, []) },
    { id: 'd', ...lin('2026-09-27T15:00:00Z', [v('2026-09-27T15:00:00Z'), v('2026-09-27T16:00:00Z')]) },
  ])
  assert.deepEqual(g.map((x) => x.dia), ['2026-09-26', '2026-09-27', ''], 'dias crescentes, sem data no fim')
  assert.deepEqual(g[1].rows.map((r) => r.id), ['a', 'd'], 'dentro do dia, a ordem da API')
  assert.equal(g[1].videos, 3)
  assert.equal(g[0].videos, 1)
}

// O aviso do vídeo que o robô vai pular (GET /fila `bloqueado`).
assert.match(H.bloqueioRotulo('arquivo_sumiu'), /sumiu/)
assert.match(H.bloqueioRotulo('codigo_novo'), /pula/, 'código desconhecido ainda avisa')
assert.equal(H.bloqueioRotulo(null), '')
assert.match(tpl, /bloqueioRotulo\(a\.bloqueado\)/, 'aviso por arquivo na fila')
assert.match(tpl, /fila: reescolha a marca/, 'linha sem marca_id ganha dica em vez do botão')

// agrupaFila: GET /fila vem um item por ARQUIVO; a tela arrasta o CRIATIVO.
{
  const f = H.agrupaFila([
    { creative_id: 'c1', file_id: 'f1', fila_posicao: 1, created_at: '2026-09-01T12:00:00Z', modelo: 'video 30s', sku: 'S1', equipe: null, file_name: 'a.mp4', pendente_em: [{ rede_id: 'r1', plataforma: 'instagram', conta: 'poofy' }] },
    { creative_id: 'c1', file_id: 'f2', fila_posicao: 1, created_at: '2026-09-01T12:00:00Z', modelo: 'video 30s', sku: 'S1', equipe: null, file_name: 'b.mp4', pendente_em: [] },
    { creative_id: 'c2', file_id: 'f3', fila_posicao: null, created_at: '2026-09-20T12:00:00Z', modelo: 'video 15s', sku: null, equipe: 'time', file_name: 'c.mp4' },
  ])
  assert.deepEqual(f.map((e) => e.creative_id), ['c1', 'c2'], 'ordem do robô, um por criativo')
  assert.deepEqual(f[0].arquivos.map((a) => a.file_id), ['f1', 'f2'])
  assert.equal(f[0].fila_posicao, 1)
  assert.equal(f[1].fila_posicao, null)
  assert.deepEqual(f[1].arquivos[0].pendente_em, [], 'pendente_em ausente vira lista vazia')
  assert.equal(f[1].arquivos[0].enviado_em, null, 'backend antigo sem enviado_em')
  assert.equal(f[1].arquivos[0].bloqueado, null)
  const b = H.agrupaFila([
    { creative_id: 'c9', file_id: 'f9', fila_posicao: 1, created_at: null, modelo: 'm', file_name: 'z.mp4', enviado_em: '2026-09-27T13:00:00Z', bloqueado: 'arquivo_sumiu', pendente_em: [] },
  ])
  assert.equal(b[0].arquivos[0].enviado_em, '2026-09-27T13:00:00Z')
  assert.equal(b[0].arquivos[0].bloqueado, 'arquivo_sumiu')
  for (const ruim of [null, undefined, {}, 'x', [{}], [{ creative_id: '' }]]) {
    assert.deepEqual(H.agrupaFila(ruim), [], `resposta ruim não derruba: ${JSON.stringify(ruim)}`)
  }
}

// moveItem: índices da lista original; fora da faixa = cópia igual.
assert.deepEqual(H.moveItem(['a', 'b', 'c'], 2, 0), ['c', 'a', 'b'])
assert.deepEqual(H.moveItem(['a', 'b', 'c'], 0, 2), ['b', 'c', 'a'])
assert.deepEqual(H.moveItem(['a', 'b', 'c'], 1, 1), ['a', 'b', 'c'])
assert.deepEqual(H.moveItem(['a', 'b', 'c'], 0, 5), ['a', 'b', 'c'])

// filaIdsAposMover: ids até o último que é o movido ou já tinha número.
{
  const e = (id, fila_posicao = null) => ({ creative_id: id, fila_posicao })
  // Nada fixo; o antigo (C) vai pra frente: só ele ganha número.
  assert.deepEqual(H.filaIdsAposMover([e('C'), e('A'), e('B')], 'C'), ['C'])
  // Desce o mais novo (A) uma casa: B precisa de número pra ficar na frente dele.
  assert.deepEqual(H.filaIdsAposMover([e('B'), e('A'), e('C')], 'A'), ['B', 'A'])
  // Entre dois fixos: os três ficam fixos, o resto segue pela data.
  assert.deepEqual(
    H.filaIdsAposMover([e('P1', 1), e('B'), e('P2', 2), e('A')], 'B'),
    ['P1', 'B', 'P2'],
  )
  // Fixo levado pro fim: tudo acima dele precisa de número.
  assert.deepEqual(
    H.filaIdsAposMover([e('P2', 2), e('A'), e('B'), e('P1', 1)], 'P1'),
    ['P2', 'A', 'B', 'P1'],
  )
  assert.deepEqual(H.filaIdsAposMover([], 'x'), [])
}

// Códigos do PUT traduzidos; os do publicar continuam valendo.
for (const code of ['criativo_de_outra_marca', 'criativo_nao_aprovado', 'forbidden']) {
  const msg = apiError.apiErrMsg({ data: { detail: { code } } }, H.FILA_ERR_MAP)
  assert.ok(msg && !msg.includes('_'), `código traduzido: ${code}`)
}
assert.match(H.FILA_ERR_MAP.criativo_nao_aprovado, /fila/, 'a frase fala da fila, não de publicar')

// ---------------------------------------------------------------- script setup
// Molde de marcas-sfc.cjs / redes-sociais-sfc.cjs: executa o <script setup>
// com api, window e timers FALSOS pra travar o fluxo do modal de ponta a ponta
// (o que sai no corpo do POST, o que trava, o que pede confirm). Nenhuma rede.
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  rows, marcas, marcaOptions, marcaValues, postagens, postagensDe, temPostagemEmVoo,
  pub, pubFiles, pubFileId, pubMarcaId, pubMarcaNome, pubContas, pubSel, pubLegenda,
  pubQuando, pubDataHora, pubShareToFeed, pubCommit, pubErr, pubTemInstagram,
  pubLegendaOrigem, pubLegendaTotal, pubLegendaIndice, pubLegendaErro, pubLegendaRotulo,
  pubSemLegenda, onLegendaInput,
  openPublicar, closePublicar, toggleConta, salvarPostagem, cancelarPostagem, loadPostagens,
  motivoPublicar, pickFile, criarRoteiroDaLinha, criandoRoteiro,
  statusFilter, statusModel, dataDe, dataAte, limparDatas, filteredRows, gruposPorDia, videosFiltrados,
  fila, filaMarca, filaMarcaId, filaErro, filaSaving, loadFila, moverFila, frenteDaFila,
  tirarDaFilaLinha, tirarDaFilaEntrada, prioritariosDaMarca, podeFurarFila, filaSemMarca,
  onFilaDragStart, onFilaDragOver, onFilaDrop, onFilaDragEnd, filaDropClass, arrastando,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'nextTick', 'onMounted', 'onBeforeUnmount', 'reactive',
  'useApi', 'useToasts', 'useAuthStore', 'useCan',
  'apiErrMsg', 'MARCAS_ERROS', 'isoToday', 'PLATAFORMA_LABELS', 'window', 'setTimeout',
  // Macro do <script setup>: o compilador do Vue resolve, o `new Function`
  // não. A célula do Roteiro emite pra página trocar de aba (migration 0299).
  'defineEmits',
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

// Resposta de GET /api/marketing/legendas/resolvida — o texto já vem
// RENDERIZADO (o {{ produto }}/{{ instagram }} resolvem no backend), que é o
// ponto: o modal mostra byte a byte o que vai pro Instagram.
const LEGENDA_FAKE = {
  texto: 'Conheça a Cafeteira Poofy — fala com a gente no @poofy',
  origem: 'marca',
  total_variacoes: 4,
  indice: 2,
}

async function tela({
  canEdit = true, linhas = [criativo()], contasResp = { commit: false, contas: CONTAS_FAKE },
  marcasErro = false, postagensIniciais = [], postError = null, confirmAnswer = true,
  legendaResp = LEGENDA_FAKE, filaResp = [], filaPutError = null,
} = {}) {
  const calls = []
  const toastLog = []
  const confirms = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marketing/creatives') return Promise.resolve(linhas.map((l) => ({ ...l })))
    if (url === '/api/marketing/creatives/equipes') return Promise.resolve(['time'])
    // Fila do robô (Eduardo, 25/09/2026): GET na ordem do robô; o PUT devolve
    // os ids gravados.
    if (url.startsWith('/api/marketing/creatives/fila')) {
      if (opts?.method === 'PUT') {
        return filaPutError ? Promise.reject(filaPutError) : Promise.resolve({ ids: [...opts.body.ids] })
      }
      return filaResp === 'erro'
        ? Promise.reject(new Error('404'))
        : Promise.resolve(filaResp.map((x) => ({ ...x })))
    }
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
    if (url.startsWith('/api/marketing/legendas/resolvida')) {
      // 'erro' = backend antigo (404) ou sem permissão (403).
      return legendaResp === 'erro'
        ? Promise.reject(new Error('404'))
        : Promise.resolve(legendaResp)
    }
    if (opts?.method === 'POST' && url === '/api/marketing/postagens') {
      return postError ? Promise.reject(postError) : Promise.resolve({})
    }
    // Roteiro criado a partir da linha (migration 0299): o POST devolve o id,
    // e o PATCH seguinte devolve a linha já com o ponteiro.
    if (opts?.method === 'POST' && url === '/api/marketing/roteiros') {
      return Promise.resolve({ id: 'rot-1' })
    }
    if (opts?.method === 'PATCH' && url.startsWith('/api/marketing/creatives/')) {
      return Promise.resolve({
        ...linhas[0], roteiro_id: opts.body.roteiro_id, roteiro_titulo: 'video 30s',
      })
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
  const emitidos = []
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
    // defineEmits: devolve o `emit`, que aqui só anota o que foi emitido.
    () => (nome, ...args) => emitidos.push([nome, ...args]),
  )
  await new Promise(setImmediate)
  return { s: state, calls, toastLog, confirms, emitidos }
}

const posts = (calls) => calls.filter((c) => c.opts?.method === 'POST')

async function run() {
  // O roteiro saiu da célula (migration 0299), mas a PORTA DE ENTRADA ficou:
  // "escrever roteiro" cria o briefing já com modelo/marca/SKU da linha,
  // vincula e manda a página abrir a aba Roteiros. Sem isto, escrever roteiro
  // pra um item passaria de um clique pra cinco passos.
  {
    const { s, calls, emitidos } = await tela()
    const linha = s.rows.value[0]
    await s.criarRoteiroDaLinha(linha)

    const criou = posts(calls).find((c) => c.url === '/api/marketing/roteiros')
    assert.ok(criou, 'cria o roteiro a partir da linha')
    assert.equal(criou.opts.body.titulo, linha.modelo, 'título vem do modelo da linha')
    assert.equal(criou.opts.body.marca, linha.marca, 'marca vem da linha')
    assert.equal(criou.opts.body.sku, linha.sku, 'SKU vem da linha — ninguém redigita')

    const vinculou = calls.find(
      (c) => c.opts?.method === 'PATCH' && c.url === `/api/marketing/creatives/${linha.id}`,
    )
    assert.ok(vinculou?.opts.body.roteiro_id, 'vincula o roteiro novo na linha')
    assert.deepEqual(emitidos[0]?.[0], 'abrir-roteiro', 'pede pra página abrir a aba Roteiros')
  }

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
  // imagem, legenda resolvida pelo backend, conta bloqueada não entra na seleção.
  {
    const { s, calls } = await tela()
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    assert.ok(s.pub.value, 'modal abriu')
    assert.equal(s.pubMarcaId.value, 'm1', 'marca casada pelo slug da coluna')
    assert.equal(s.pubMarcaNome.value, 'Poofy')
    assert.equal(s.pubFileId.value, 'fvid', 'vídeo na frente da imagem (o robô publica Reels)')
    assert.equal(s.pubLegenda.value, LEGENDA_FAKE.texto, 'legenda resolvida, já renderizada')
    assert.notEqual(s.pubLegenda.value, s.rows.value[0].roteiro, 'roteiro nunca vira legenda')
    assert.equal(s.pubLegendaOrigem.value, 'marca')
    assert.equal(s.pubSemLegenda.value, false, 'com texto, nada de aviso âmbar')
    assert.match(s.pubLegendaRotulo.value, /^padrão da marca · variação 2 de 4 · \d+\/2200$/)
    assert.equal(s.pubQuando.value, 'agora')
    assert.match(s.pubDataHora.value, /^\d{4}-\d{2}-\d{2}T\d{2}:00$/, 'data/hora default é hora cheia')
    assert.equal(s.pubCommit.value, false, 'modo seco veio do servidor')
    assert.deepEqual(s.pubSel.value, ['r1'], 'única conta liberada já vem marcada')
    assert.equal(s.pubTemInstagram.value, true)
    // creative_id + file_id vão junto: é o que faz o backend responder com o
    // mesmo veredito do publicador (teto do dia, vídeo repetido, em voo).
    const contasUrl = calls.map((c) => c.url).filter((u) => u.startsWith('/api/marketing/postagens/contas?')).at(-1)
    assert.ok(contasUrl, 'pediu as contas da marca')
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

  // Legenda resolvida: a conta marcada entra na consulta, porque é o @ dela
  // que o modelo usa — trocar de conta troca o texto.
  {
    const { s, calls } = await tela({
      contasResp: { commit: true, contas: CONTAS_FAKE.map((c) => ({ ...c, pode_postar: true, motivo: null })) },
    })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    const legendas = () => calls.map((c) => c.url).filter((u) => u.startsWith('/api/marketing/legendas/resolvida'))
    const primeira = legendas().at(-1)
    assert.match(primeira, /creative_id=c1/)
    assert.match(primeira, /file_id=fvid/)
    // Duas contas liberadas = nenhuma marcada: sem conta, sem `rede_social_id`
    // (o backend ainda resolve o padrão da marca, só não sabe o @).
    assert.ok(!primeira.includes('rede_social_id'), primeira)
    s.toggleConta(s.pubContas.value[0])
    await new Promise(setImmediate)
    assert.match(legendas().at(-1), /rede_social_id=r1/, 'marcar a conta refaz a consulta')
    assert.equal(legendas().length, 2)
  }

  // Legenda editada à mão: vira `manual`, perde a variação (não é mais o
  // modelo do cadastro) e NÃO é sobrescrita quando a conta muda — o texto do
  // operador é dele.
  {
    const { s, calls } = await tela({
      contasResp: { commit: true, contas: CONTAS_FAKE.map((c) => ({ ...c, pode_postar: true, motivo: null })) },
    })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    s.onLegendaInput({ target: { value: 'texto que o operador escreveu' } })
    assert.equal(s.pubLegenda.value, 'texto que o operador escreveu')
    assert.equal(s.pubLegendaOrigem.value, 'manual')
    assert.equal(s.pubLegendaRotulo.value, 'escrita agora · 29/2200')
    const antes = calls.filter((c) => c.url.startsWith('/api/marketing/legendas/resolvida')).length
    s.toggleConta(s.pubContas.value[0])
    await new Promise(setImmediate)
    assert.equal(s.pubLegenda.value, 'texto que o operador escreveu', 'trocar de conta não apaga o que ele digitou')
    assert.equal(
      calls.filter((c) => c.url.startsWith('/api/marketing/legendas/resolvida')).length,
      antes,
      'nem vai ao servidor de novo',
    )
    // É o texto editado que vai no POST.
    await s.salvarPostagem()
    assert.equal(posts(calls).at(-1).opts.body.legenda, 'texto que o operador escreveu')

    // Apagar tudo à mão volta pro estado "sem legenda" (e ao aviso âmbar).
    const { s: s2 } = await tela()
    s2.openPublicar(s2.rows.value[0])
    await new Promise(setImmediate)
    s2.onLegendaInput({ target: { value: '   ' } })
    assert.equal(s2.pubLegendaOrigem.value, 'nenhuma')
    assert.equal(s2.pubSemLegenda.value, true)
    assert.match(s2.pubLegendaRotulo.value, /^sem legenda · 3\/2200$/)
  }

  // Endpoint fora do ar (404 do backend antigo / 403 sem permissão): textarea
  // VAZIO, nunca o roteiro, e a tela continua inteira.
  {
    const { s } = await tela({ legendaResp: 'erro' })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    assert.equal(s.pubLegenda.value, '', 'cai pro textarea vazio')
    assert.equal(s.pubLegendaErro.value, true, 'a tela conta por que o campo veio vazio')
    assert.equal(s.pubSemLegenda.value, true)
    assert.ok(s.pub.value, 'modal continua aberto e usável')
    assert.deepEqual(s.pubSel.value, ['r1'], 'as contas carregaram do mesmo jeito')
  }

  // Publicar SEM legenda: confirmação a mais. Reel mudo é criativo queimado —
  // a legenda é o único texto que a busca do Instagram lê do vídeo.
  {
    const vazia = { texto: '', origem: 'nenhuma', total_variacoes: 0, indice: 0 }
    const { s, calls, confirms } = await tela({ legendaResp: vazia, confirmAnswer: false })
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    assert.equal(s.pubSemLegenda.value, true)
    assert.match(s.pubLegendaRotulo.value, /^sem legenda · 0\/2200$/)
    await s.salvarPostagem()
    assert.equal(confirms.length, 1, 'perguntou antes')
    assert.match(confirms[0], /sem legenda/i)
    assert.ok(!/senha|token/i.test(confirms[0]), 'confirm nunca fala de credencial')
    assert.equal(posts(calls).length, 0, 'recusar não publica nada')

    const { s: s2, calls: c2, confirms: cf2 } = await tela({ legendaResp: vazia })
    s2.openPublicar(s2.rows.value[0])
    await new Promise(setImmediate)
    await s2.salvarPostagem()
    assert.equal(cf2.length, 1)
    assert.equal(posts(c2).at(-1).opts.body.legenda, null, 'confirmado, vai com legenda null')

    // Com legenda não pergunta nada — a confirmação extra é só pro post mudo.
    const { s: s3, confirms: cf3 } = await tela()
    s3.openPublicar(s3.rows.value[0])
    await new Promise(setImmediate)
    await s3.salvarPostagem()
    assert.equal(cf3.length, 0)
  }

  // Agendar: POST com o corpo certo, toast, modal fecha e a agenda recarrega.
  {
    const { s, calls, toastLog } = await tela()
    s.openPublicar(s.rows.value[0])
    await new Promise(setImmediate)
    s.pubQuando.value = 'agendar'
    s.pubDataHora.value = '2026-09-16T14:30'
    // Digitar de verdade (o handler é quem marca origem=manual) — setar o ref
    // na mão provaria um caminho que não existe na tela.
    s.onLegendaInput({ target: { value: '  vem ver esse produto  ' } })
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

  // ------------------------------------------------ filtro por dia (25/09/2026)
  {
    const vid = (id) => ({ id, file_name: `${id}.mp4`, file_mime: 'video/mp4', file_size: 1 })
    const linhas = [
      criativo({ id: 'd1', created_at: '2026-09-24T15:00:00Z', files: [vid('v1')] }),
      criativo({ id: 'd2', created_at: '2026-09-25T13:00:00Z', files: [vid('v2'), vid('v3')] }),
      criativo({ id: 'd3', created_at: '2026-09-26T01:30:00Z', files: [vid('v4')] }), // 25/09 22h30 BRT
      criativo({ id: 'd4', created_at: null, files: [] }),
    ]
    const { s } = await tela({ linhas })
    assert.deepEqual(s.gruposPorDia.value.map((g) => g.dia), ['2026-09-24', '2026-09-25', ''])
    assert.deepEqual(s.gruposPorDia.value[1].rows.map((r) => r.id), ['d2', 'd3'], 'ordem da API mantida')
    assert.equal(s.videosFiltrados.value, 4)

    s.dataDe.value = '2026-09-25'
    s.dataAte.value = '2026-09-25'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['d2', 'd3'], 'dia BRT, não UTC; sem data fica fora')
    assert.equal(s.videosFiltrados.value, 3)
    assert.equal(s.gruposPorDia.value.length, 1)

    s.dataAte.value = ''
    s.statusFilter.value = 'reprovado'
    assert.deepEqual(s.filteredRows.value, [], 'data soma com o filtro de status')
    s.statusFilter.value = 'todos'

    s.limparDatas()
    assert.equal(s.filteredRows.value.length, 4, 'limpar datas volta tudo')
  }

  // O filtro olha o dia em que o VÍDEO chegou: linha aberta dia 25 com vídeo
  // do dia 27 é do dia 27.
  {
    const linhas = [
      criativo({
        id: 'tarde', created_at: '2026-09-25T13:00:00Z',
        files: [{ id: 'vt', file_name: 't.mp4', file_mime: 'video/mp4', file_size: 1, enviado_em: '2026-09-27T13:00:00Z' }],
      }),
      criativo({
        id: 'mesmo', created_at: '2026-09-25T14:00:00Z',
        files: [{ id: 'vm', file_name: 'm.mp4', file_mime: 'video/mp4', file_size: 1, enviado_em: '2026-09-25T14:05:00Z' }],
      }),
    ]
    const { s } = await tela({ linhas })
    s.dataDe.value = '2026-09-25'
    s.dataAte.value = '2026-09-25'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['mesmo'])
    s.dataDe.value = '2026-09-27'
    s.dataAte.value = '2026-09-27'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['tarde'])
    assert.equal(s.gruposPorDia.value[0].videos, 1)
  }

  // ------------------------------------------------ fila do robô (25/09/2026)
  const FILA_FAKE = [
    { creative_id: 'n1', file_id: 'fn1', fila_posicao: null, created_at: '2026-09-25T12:00:00Z', modelo: 'video 30s', sku: 'N1', equipe: null, file_name: 'n1.mp4', pendente_em: [{ rede_id: 'r1', plataforma: 'instagram', conta: 'poofy' }] },
    { creative_id: 'n2', file_id: 'fn2', fila_posicao: null, created_at: '2026-09-20T12:00:00Z', modelo: 'video 30s', sku: 'N2', equipe: null, file_name: 'n2.mp4', pendente_em: [{ rede_id: 'r1', plataforma: 'instagram', conta: 'poofy' }] },
    { creative_id: 'velho', file_id: 'fv', fila_posicao: null, created_at: '2026-08-01T12:00:00Z', modelo: 'video 15s', sku: 'V', equipe: null, file_name: 'velho.mp4', pendente_em: [{ rede_id: 'r1', plataforma: 'instagram', conta: 'poofy' }] },
  ]
  const puts = (calls) => calls.filter((c) => c.opts?.method === 'PUT')
  const dragEv = () => ({
    prevented: false,
    preventDefault() { this.prevented = true },
    dataTransfer: { effectAllowed: '', dropEffect: '', setData() {} },
  })

  // Escolher "fila do robô" já carrega a fila da primeira marca.
  {
    const { s, calls } = await tela({ filaResp: FILA_FAKE })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    assert.equal(s.filaMarcaId.value, 'm1')
    assert.ok(calls.some((c) => c.url === '/api/marketing/creatives/fila?marca_id=m1'), 'GET /fila?marca_id=')
    assert.deepEqual(s.fila.value.map((e) => e.creative_id), ['n1', 'n2', 'velho'])

    // Arrastar o antigo pra frente: só ele ganha número (os outros seguem pela data).
    const ev = dragEv()
    s.onFilaDragStart(2, ev)
    const over = dragEv()
    s.onFilaDragOver(0, over)
    assert.equal(over.prevented, true, 'aceita soltar o item da própria lista')
    assert.match(s.filaDropClass(0), /\[&>td\]:border-t-2/, 'indicador acima quando sobe (nas células)')
    s.onFilaDrop(0)
    await new Promise(setImmediate)
    await new Promise(setImmediate)
    const put = puts(calls).at(-1)
    assert.equal(put.url, '/api/marketing/creatives/fila')
    assert.deepEqual(put.opts.body, { marca_id: 'm1', ids: ['velho'] })
    assert.equal(s.arrastando.value, null, 'arraste zerado depois de soltar')
    const gets = calls.filter((c) => c.url.startsWith('/api/marketing/creatives/fila?') && !c.opts?.method)
    assert.equal(gets.length, 2, 'recarrega a fila depois do PUT')
  }

  // Arraste que não saiu da lista (arquivo do Finder, texto) não é aceito.
  {
    const { s } = await tela({ filaResp: FILA_FAKE })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    const over = dragEv()
    s.onFilaDragOver(0, over)
    assert.equal(over.prevented, false)
  }

  // ↓ no mais novo: o de baixo sobe e precisa de número pra ficar na frente.
  {
    const { s, calls } = await tela({ filaResp: FILA_FAKE })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    await s.moverFila(0, 1)
    assert.deepEqual(puts(calls).at(-1).opts.body.ids, ['n2', 'n1'])
  }

  // PUT falhou: a lista volta como estava e o erro aparece traduzido.
  {
    const { s, calls, toastLog } = await tela({
      filaResp: FILA_FAKE,
      filaPutError: { data: { detail: { code: 'criativo_nao_aprovado' } } },
    })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    await s.moverFila(2, 0)
    assert.equal(puts(calls).length, 1)
    assert.deepEqual(s.fila.value.map((e) => e.creative_id), ['n1', 'n2', 'velho'], 'rollback')
    assert.equal(s.fila.value[2].fila_posicao, null, 'o número otimista some junto')
    const erro = toastLog.find((t) => t[0] === 'error')
    assert.ok(erro, 'toast de erro')
    assert.match(erro[2], /aprovado/)
    assert.equal(s.filaSaving.value, false)
  }

  // Sem permissão de edição: nada de PUT, nem arrastando.
  {
    const { s, calls } = await tela({ canEdit: false, filaResp: FILA_FAKE })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    await s.moverFila(2, 0)
    const ev = dragEv()
    s.onFilaDragStart(2, ev)
    assert.equal(ev.prevented, true)
    assert.equal(puts(calls).length, 0)
  }

  // GET /fila fora do ar: mensagem na tela, sem derrubar nada.
  {
    const { s } = await tela({ filaResp: 'erro' })
    s.statusModel.value = 'fila'
    await new Promise(setImmediate)
    assert.deepEqual(s.fila.value, [])
    assert.ok(s.filaErro.value)
  }

  // Planilha: "⤒ frente da fila" põe a linha em 1º e empurra quem já tinha número.
  {
    const vid = [{ id: 'fv', file_name: 'x.mp4', file_mime: 'video/mp4', file_size: 1 }]
    const linhas = [
      criativo({ id: 'p1', marca_id: 'm1', files: vid, fila_posicao: 1 }),
      criativo({ id: 'p2', marca_id: 'm1', files: vid, fila_posicao: 2 }),
      criativo({ id: 'x', marca_id: 'm1', files: vid, fila_posicao: null }),
      // Perdeu a aprovação com número velho: não pode ir no PUT (daria 422).
      criativo({ id: 'reprov', marca_id: 'm1', files: vid, fila_posicao: 3, aprovado: null }),
      // Outra marca: a fila dela não é tocada.
      criativo({ id: 'outra', marca_id: 'm2', files: vid, fila_posicao: 1 }),
      criativo({ id: 'img', marca_id: 'm1', files: [{ id: 'fi', file_name: 'a.png', file_mime: 'image/png', file_size: 1 }] }),
    ]
    const { s, calls, toastLog } = await tela({ linhas })
    const linha = (id) => s.rows.value.find((r) => r.id === id)
    assert.deepEqual(s.prioritariosDaMarca('m1'), ['p1', 'p2'])
    assert.equal(s.podeFurarFila(linha('x')), true)
    assert.equal(s.podeFurarFila(linha('img')), false, 'o robô só posta vídeo')
    assert.equal(s.podeFurarFila(linha('reprov')), false, 'só aprovado')

    await s.frenteDaFila(linha('x'))
    assert.deepEqual(puts(calls).at(-1).opts.body, { marca_id: 'm1', ids: ['x', 'p1', 'p2'] })
    assert.equal(linha('x').fila_posicao, 1)
    assert.equal(linha('p1').fila_posicao, 2)
    assert.equal(linha('p2').fila_posicao, 3)
    assert.equal(linha('reprov').fila_posicao, null, 'o servidor zera quem ficou de fora')
    assert.equal(linha('outra').fila_posicao, 1, 'outra marca intacta')
    assert.equal(toastLog.at(-1)[0], 'success')

    // O × do selo tira da frente e mantém a ordem dos outros.
    await s.tirarDaFilaLinha(linha('p1'))
    await new Promise(setImmediate)
    assert.deepEqual(puts(calls).at(-1).opts.body, { marca_id: 'm1', ids: ['x', 'p2'] })
    assert.equal(linha('p1').fila_posicao, null)
    assert.equal(linha('p2').fila_posicao, 2)

    // Linha com o texto da marca mas SEM marca_id: o robô e o PUT só olham o
    // marca_id, então nada de botão (o PUT voltaria criativo_de_outra_marca).
    {
      const semId = criativo({ id: 'semid', marca: 'poofy', marca_id: null, files: vid })
      s.rows.value.push(semId)
      const r = s.rows.value.find((x) => x.id === 'semid')
      assert.equal(s.podeFurarFila(r), false, 'sem marca_id não fura a fila')
      assert.equal(s.filaSemMarca(r), true, 'mostra a dica de reescolher a marca')
      assert.deepEqual(s.prioritariosDaMarca('m1'), ['x', 'p2'], 'não entra na conta da marca pelo texto')
      const antes = puts(calls).length
      await s.frenteDaFila(r)
      assert.equal(puts(calls).length, antes, 'nenhum PUT')
      assert.equal(toastLog.at(-1)[0], 'warning')
      s.rows.value.splice(s.rows.value.indexOf(r), 1)
    }

    // Tirar o último: lista vazia limpa a fila da marca.
    await s.tirarDaFilaLinha(linha('x'))
    await new Promise(setImmediate)
    await s.tirarDaFilaLinha(linha('p2'))
    await new Promise(setImmediate)
    assert.deepEqual(puts(calls).at(-1).opts.body, { marca_id: 'm1', ids: [] })
  }
}

run().then(() => {
  console.log('PASS: SFC parse + template compile; higiene (sem token/senha, rel=noopener, confirm no cancelar, roteiro fora da legenda); helpers puros (BRT→ISO, corpo do POST, contas, pills, motivos, rótulo/origem da legenda, dia BRT/cabeçalho do dia, fila do robô); script setup com api falso (modal, travas, legenda resolvida, edição manual, endpoint fora do ar, confirm do post sem legenda, cancelar, filtro por dia, arrastar/↑↓ na fila com rollback, frente da fila na planilha)')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
