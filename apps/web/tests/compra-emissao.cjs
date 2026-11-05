// Run from apps/web: node tests/compra-emissao.cjs
// Contabilidade › Compra (04/11/2026): emissão AUTOMÁTICA da NF de compra pelo
// robô do Mac. As travas que importam na tela:
//  - ROTULO_STATUS cobre os 6 status do servidor (sem isso a lista quebra no
//    1º status novo: `ROTULO_STATUS[c.status].classe` de undefined);
//  - número "queimado" (o robô já tentou importar: o pedido 800000x pode estar
//    no Bling) nunca mais tem planilha, editar, reabrir, excluir nem emitir —
//    planilha importada à mão por cima = NF em dobro;
//  - vendedora manual nunca tem "emitir";
//  - Emitir pede EMITIR digitado e, no 409 planilha_ja_baixada/outra_no_mes,
//    pergunta de novo e reenvia com a flag (uma vez cada);
//  - a lista se relê a cada 30 s só enquanto houver compra na fila/com o robô,
//    e para fora da aba (KeepAlive) e ao desmontar.
//  - imprimir a NF (04/11, "igual emissão de serviço"): NF emitida com a chave tem
//    "imprimir a NF (DANFE)" e "baixar PDF" no menu; a caixinha marca OU as sem NF
//    (planilha) OU as NFs (imprimir / baixar PDFs / XMLs na barra de baixo), nunca as
//    duas; o lote vai pelas rotas da Compra com os mesmos campos da NFS-e (OrigemLote).
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { ref, computed, watch, nextTick } = requireWeb('vue')
const ts = requireWeb('typescript')
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')

const transpilar = (fonte) =>
  ts.transpileModule(fonte, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText

// ---------------------------------------------------------------- lib/compra.ts solta
const fonteLib = fs.readFileSync(path.join(__dirname, '..', 'lib', 'compra.ts'), 'utf8')
assert.ok(!/^import /m.test(fonteLib), 'lib sem import (o teste roda ela solta)')
// lib/ não é varrido pelo Tailwind: só as classes pill-* (definidas no globals.css) podem morar aqui
assert.ok(!/['"`][^'"`]*\b(text|bg|border)-(emerald|red|amber|muted|primary)/.test(fonteLib), 'nenhuma classe do Tailwind na lib')
const L = {}
new Function('exports', 'require', transpilar(fonteLib))(L, () => { throw new Error('lib/compra.ts não pode importar nada') })

const STATUS = ['registrada', 'na_fila', 'emitindo', 'nf_emitida', 'erro', 'cancelada']
const ETAPAS = ['lease', 'import_tentado', 'importado', 'nf_gerada', 'pronta_sefaz', 'sefaz_tentado', 'autorizada', 'xml_ok', 'devolvido']

// ---------------------------------------------------------------- status e etapas
{
  const tipo = fonteLib.match(/export type StatusCompra =([^\n]+)/)[1]
  assert.deepEqual([...tipo.matchAll(/'(\w+)'/g)].map((m) => m[1]), STATUS, 'StatusCompra tem os 6 status')
  assert.deepEqual(Object.keys(L.ROTULO_STATUS).sort(), [...STATUS].sort(), 'ROTULO_STATUS cobre os 6 status')
  for (const s of STATUS) {
    const r = L.ROTULO_STATUS[s]
    assert.ok(r.texto && r.texto.trim(), `${s} tem texto`)
    assert.match(r.classe, /^pill-(success|warning|danger|muted|info)$/, `${s} usa uma pill do globals.css`)
  }
  assert.equal(L.ROTULO_STATUS.na_fila.classe, 'pill-info')
  assert.equal(L.ROTULO_STATUS.emitindo.classe, 'pill-info')
  assert.equal(L.ROTULO_STATUS.erro.classe, 'pill-danger')

  // os mesmos do servidor (models/compra.py); ETAPAS entra com a migration 0450
  const modelo = fs.readFileSync(path.join(__dirname, '..', '..', 'api', 'app', 'models', 'compra.py'), 'utf8')
  const tupla = (nome) => {
    const m = modelo.match(new RegExp(`^${nome}\\s*=\\s*\\(([^)]*)\\)`, 'm'))
    return m ? [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]) : null
  }
  assert.deepEqual([...tupla('STATUS')].sort(), [...STATUS].sort(), 'STATUS igual ao do servidor')
  const etapasServidor = tupla('ETAPAS')
  if (etapasServidor) assert.deepEqual(etapasServidor, ETAPAS, 'ETAPAS igual (e na mesma ordem) ao servidor')

  assert.deepEqual([...L.ETAPAS_EMISSAO], ETAPAS, 'etapas na ordem do plano')
  assert.deepEqual(Object.keys(L.ROTULO_ETAPA), ETAPAS, 'cada etapa tem rótulo')
  for (const e of ETAPAS) assert.ok(L.ROTULO_ETAPA[e].trim(), `rótulo de ${e}`)
}

// ---------------------------------------------------------------- número queimado
{
  assert.equal(L.etapaQueimada(null), false)
  assert.equal(L.etapaQueimada(undefined), false)
  assert.equal(L.etapaQueimada('lease'), false, 'só pegou: nada no Bling ainda')
  assert.equal(L.etapaQueimada('qualquer'), false, 'etapa desconhecida não queima')
  for (const e of ETAPAS.slice(1)) assert.equal(L.etapaQueimada(e), true, `${e} queima o número`)
  assert.equal(L.numeroQueimado({ numero_queimado: true, emissao_etapa: null }), true, 'o servidor manda')
  assert.equal(L.numeroQueimado({ numero_queimado: false, emissao_etapa: 'importado' }), true, 'a etapa também basta')
  assert.equal(L.numeroQueimado({ numero_queimado: undefined, emissao_etapa: 'lease' }), false, 'servidor antigo sem o campo')
}

// ---------------------------------------------------------------- vendedora automática
{
  const BEZERRA = { cnpj: '67285941000190', automatico: true, adspower_serial: 'k13ksbpr', ativo: true }
  assert.equal(L.vendedoraAutomatica(BEZERRA), true)
  assert.equal(L.vendedoraAutomatica({ ...BEZERRA, ativo: undefined }), true, 'sem o campo ativo = ativa')
  assert.equal(L.vendedoraAutomatica({ ...BEZERRA, automatico: false }), false, 'TELES/DL/PEDRO: manual')
  assert.equal(L.vendedoraAutomatica({ ...BEZERRA, adspower_serial: null }), false, 'sem perfil do AdsPower o robô não tem onde abrir')
  assert.equal(L.vendedoraAutomatica({ ...BEZERRA, adspower_serial: '' }), false)
  assert.equal(L.vendedoraAutomatica({ ...BEZERRA, ativo: false }), false)
  assert.equal(L.vendedoraAutomatica(null), false)
}

// ---------------------------------------------------------------- menu ⋯ (acoesDaCompra)
const PRECISA_EDIT = ['emitir', 'planilha', 'nf', 'trocar_nf', 'editar', 'cancelar', 'cancelar_conferido', 'tirar_fila', 'desfazer', 'desfazer_conferido', 'reabrir']
const PRECISA_DELETE = ['excluir', 'parar_robo']
const PROIBIDAS_QUEIMADO = ['planilha', 'editar', 'reabrir', 'excluir', 'emitir']
{
  const combos = []
  for (const status of STATUS) {
    for (const emissao_etapa of [null, ...ETAPAS]) {
      for (const numero_queimado of [false, true]) {
        for (const nf_chave of [null, '35251167285941000190550010000030311000030316']) {
          for (const canEdit of [false, true]) {
            for (const canDelete of [false, true]) {
              for (const automatica of [false, true]) {
                combos.push({ c: { status, emissao_etapa, numero_queimado, nf_chave }, o: { canEdit, canDelete, automatica } })
              }
            }
          }
        }
      }
    }
  }
  for (const { c, o } of combos) {
    const a = L.acoesDaCompra(c, o)
    const onde = JSON.stringify({ ...c, ...o })
    assert.equal(new Set(a).size, a.length, `sem repetir: ${onde}`)
    const queimado = c.numero_queimado || L.etapaQueimada(c.emissao_etapa)
    if (queimado) for (const x of PROIBIDAS_QUEIMADO) assert.ok(!a.includes(x), `queimado sem ${x}: ${onde}`)
    if (!o.automatica) assert.ok(!a.includes('emitir'), `vendedora manual nunca emite: ${onde}`)
    if (!o.canEdit) for (const x of PRECISA_EDIT) assert.ok(!a.includes(x), `sem edit, sem ${x}: ${onde}`)
    if (!o.canDelete) for (const x of PRECISA_DELETE) assert.ok(!a.includes(x), `sem delete, sem ${x}: ${onde}`)
    if (a.includes('emitir')) assert.equal(a[0], 'emitir', `emitir vem primeiro: ${onde}`)
    // emitir e planilha só sem NF e fora do robô: registrada, ou erro de antes do import
    const semNf = !queimado && (c.status === 'registrada' || c.status === 'erro')
    if (!semNf) {
      assert.ok(!a.includes('emitir'), `${c.status} não emite: ${onde}`)
      assert.ok(!a.includes('planilha'), `${c.status} não baixa planilha: ${onde}`)
      assert.ok(!a.includes('editar'), `${c.status} não edita: ${onde}`)
    }
    if (c.status === 'na_fila') assert.deepEqual(a, o.canEdit ? ['tirar_fila'] : [], `na fila: só tirar da fila: ${onde}`)
    if (c.status === 'emitindo') assert.deepEqual(a, o.canDelete ? ['parar_robo'] : [], `com o robô: só tirar do robô: ${onde}`)
    if (c.status === 'cancelada') assert.ok(!a.includes('emitir') && !a.includes('planilha'), `cancelada: ${onde}`)
    if (a.includes('xml')) assert.ok(c.nf_chave && ['nf_emitida', 'cancelada'].includes(c.status), `XML só com chave: ${onde}`)
    if (a.includes('chave')) assert.ok(c.nf_chave, `copiar chave só com chave: ${onde}`)
    // igual ao servidor (desfazer_nf): decide pelo número queimado, não por "tem etapa"
    if (a.includes('desfazer_conferido')) assert.ok(c.status === 'nf_emitida' && queimado, `NF do robô: ${onde}`)
    if (a.includes('desfazer')) assert.ok(c.status === 'nf_emitida' && !queimado, `NF manual: ${onde}`)
    // (registrada queimada não existe no servidor; se vier, a tela trata como o erro queimado)
    if (a.includes('cancelar_conferido')) assert.ok(['erro', 'registrada'].includes(c.status) && queimado, `cancelar conferido só no erro queimado: ${onde}`)
    if (a.includes('cancelar')) assert.ok(!queimado, `queimado não cancela sem conferir: ${onde}`)
    if (a.includes('trocar_nf')) assert.ok(!c.nf_chave, `a NF com chave do XML não troca de número: ${onde}`)
    // DANFE (04/11): só NF emitida com a chave — a cancelada foi desfeita no Bling; quem só vê também
    const temDanfe = c.status === 'nf_emitida' && !!c.nf_chave
    assert.equal(a.includes('imprimir'), temDanfe, `imprimir a DANFE só da NF emitida com chave: ${onde}`)
    assert.equal(a.includes('pdf'), temDanfe, `baixar o PDF só da NF emitida com chave: ${onde}`)
    if (temDanfe) assert.equal(a[0], 'imprimir', `imprimir vem primeiro: ${onde}`)
  }

  const base = { emissao_etapa: null, numero_queimado: false, nf_chave: null }
  const tudo = { canEdit: true, canDelete: true, automatica: true }
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'registrada' }, tudo), ['emitir', 'planilha', 'nf', 'editar', 'cancelar', 'excluir'])
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'registrada' }, { ...tudo, automatica: false }), ['planilha', 'nf', 'editar', 'cancelar', 'excluir'], 'manual: igual a antes')
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'erro', emissao_etapa: 'lease' }, tudo), ['emitir', 'planilha', 'nf', 'editar', 'cancelar', 'excluir'], 'erro antes do import = sem NF')
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'erro', emissao_etapa: 'sefaz_tentado', numero_queimado: true }, tudo), ['nf', 'cancelar_conferido'])
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'nf_emitida', nf_chave: '3525', emissao_etapa: 'devolvido', numero_queimado: true }, tudo), ['imprimir', 'pdf', 'xml', 'chave', 'desfazer_conferido'])
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'nf_emitida' }, tudo), ['trocar_nf', 'desfazer'], 'NF anotada à mão: igual a antes')
  // o robô parou antes do import e a NF foi anotada à mão: o servidor devolve para "sem NF"
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'nf_emitida', emissao_etapa: 'lease' }, tudo), ['trocar_nf', 'desfazer'], 'parou no lease: desfazer comum')
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'cancelada' }, tudo), ['reabrir', 'excluir'])
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'cancelada', nf_chave: '3525', emissao_etapa: 'devolvido', numero_queimado: true }, tudo), ['xml', 'chave'], 'NF do robô desfeita: só consulta')
  assert.deepEqual(L.acoesDaCompra({ ...base, status: 'nf_emitida', nf_chave: '3525', emissao_etapa: 'devolvido' }, { canEdit: false, canDelete: false, automatica: true }), ['imprimir', 'pdf', 'xml', 'chave'], 'quem só vê imprime e baixa')
}

assert.equal(L.chaveCurta('35251167285941000190550010000030311000030316'), '3525…030316')
// do envio à SEFAZ em diante: nunca reenviar
for (const e of ETAPAS) assert.equal(L.passouDaSefaz(e), ETAPAS.indexOf(e) >= ETAPAS.indexOf('sefaz_tentado'), `passouDaSefaz(${e})`)
assert.equal(L.passouDaSefaz(null), false)
// a chave que o robô deixou no texto do erro
assert.equal(L.chaveDoErro('XML não confere (chave 35261167285941000190550010000030311000030318)'), '35261167285941000190550010000030311000030318')
assert.equal(L.chaveDoErro('perfil não abriu'), '')
assert.equal(L.chaveDoErro(null), '')
assert.equal(L.chaveCurta(null), '')

// ---------------------------------------------------------------- imprimir a NF (04/11)
{
  const CH = '35261167285941000190550010000030311000030318' // NF 3031
  // o nome do arquivo igual ao servidor (services/compra_arquivos.numero_da_nf)
  assert.equal(L.numeroDaNf({ nf_numero: '3031', nf_chave: null }), '3031')
  assert.equal(L.numeroDaNf({ nf_numero: ' 3.031 ', nf_chave: null }), '3031', 'só os números')
  assert.equal(L.numeroDaNf({ nf_numero: null, nf_chave: CH }), '3031', 'sem o nº, o da chave')
  assert.equal(L.numeroDaNf({ nf_numero: null, nf_chave: '123' }), 'sem-numero')
  assert.equal(L.nomeDanfe({ nf_numero: '3031', nf_chave: CH }), 'danfe_nf3031.pdf')
  const servidor = fs.readFileSync(path.join(__dirname, '..', '..', 'api', 'app', 'services', 'compra_arquivos.py'), 'utf8')
  assert.match(servidor, /return f"danfe_nf\{numero_da_nf\(p\)\}\.pdf"/, 'o mesmo nome no servidor')
  assert.match(servidor, /return "sem-numero"/)

  // a caixinha: sem NF (planilha) | NF emitida com a chave (imprimir/baixar) | nada
  for (const status of STATUS) {
    for (const nf_chave of [null, CH]) {
      for (const emissao_etapa of [null, 'lease', 'importado', 'devolvido']) {
        const c = { status, nf_chave, emissao_etapa, numero_queimado: false }
        const g = L.grupoDaMarcacao(c)
        const queimado = L.etapaQueimada(emissao_etapa)
        const esperado = status === 'registrada' && !queimado ? 'planilha' : status === 'nf_emitida' && nf_chave ? 'nf' : null
        assert.equal(g, esperado, JSON.stringify(c))
      }
    }
  }
  assert.equal(L.grupoDaMarcacao({ status: 'registrada', nf_chave: null, emissao_etapa: null, numero_queimado: true }), null, 'queimada nunca vai à planilha')
  assert.equal(L.grupoDaMarcacao({ status: 'cancelada', nf_chave: CH, emissao_etapa: 'devolvido', numero_queimado: true }), null, 'NF desfeita não imprime')

  // o lote da Compra tem os MESMOS campos da OrigemLote (lib/nfse.ts) e as rotas existem no servidor
  const libNfse = fs.readFileSync(path.join(__dirname, '..', 'lib', 'nfse.ts'), 'utf8')
  const campos = [...libNfse.match(/export type OrigemLote = \{([\s\S]*?)\n\}/)[1].matchAll(/^\s+(\w+): string/gm)].map((m) => m[1])
  assert.ok(campos.length >= 8, 'OrigemLote lida')
  assert.deepEqual(Object.keys(L.LOTE_COMPRA).sort(), [...campos].sort(), 'LOTE_COMPRA = OrigemLote')
  for (const k of campos) assert.ok(typeof L.LOTE_COMPRA[k] === 'string' && L.LOTE_COMPRA[k].trim(), `LOTE_COMPRA.${k}`)
  assert.equal(L.LOTE_COMPRA.rota, '/api/nfse/compras/lote')
  assert.match(libNfse, /rota: '\/api\/nfse\/emissoes\/lote'/, 'a NFS-e segue nas rotas dela')
  const router = fs.readFileSync(path.join(__dirname, '..', '..', 'api', 'app', 'routers', 'nfse_compras.py'), 'utf8')
  assert.match(router, /@router\.post\("\/lote\/imprimir"\)/)
  assert.match(router, /@router\.post\("\/lote\/arquivos"\)/)
  assert.match(router, /@router\.get\("\/\{pedido_id\}\/pdf"\)/)
  // o aviso das que faltaram: "nº 3031 (vita)"
  assert.deepEqual(L.paraLote({ id: 'c1', nf_numero: '3031', comprador: { apelido: 'vita', razao_social: 'VITA LTDA' } }), { id: 'c1', n_nfse: '3031', prestador_nome: 'vita' })
  assert.deepEqual(L.paraLote({ id: 'c2', nf_numero: null, comprador: { apelido: null, razao_social: 'RIO LTDA' } }), { id: 'c2', n_nfse: null, prestador_nome: 'RIO LTDA' })
}

// ---------------------------------------------------------------- CompraLista.vue
const arqLista = path.resolve(__dirname, '../components/CompraLista.vue')
const lista = parse(fs.readFileSync(arqLista, 'utf8'), { filename: arqLista })
assert.deepEqual(lista.errors, [])
assert.deepEqual(compileTemplate({ source: lista.descriptor.template.content, filename: arqLista, id: 'compra-lista' }).errors, [])
const scriptLista = lista.descriptor.scriptSetup.content
const tplLista = lista.descriptor.template.content
const bloco = (de, ate) => {
  const i = scriptLista.indexOf(de)
  const j = scriptLista.indexOf(ate, i)
  assert.ok(i >= 0 && j > i, `bloco "${de}" … "${ate}"`)
  return scriptLista.slice(i, j)
}
{
  // o menu sai da função testada acima, e cada ação tem rótulo
  assert.match(scriptLista, /acoesDaCompra\(c, \{/, 'menu vem de acoesDaCompra')
  const uniao = fonteLib.match(/export type AcaoCompra =([\s\S]*?)\n\n/)[1]
  const acoes = [...uniao.matchAll(/'(\w+)'/g)].map((m) => m[1])
  const menu = bloco('const MENU', '\n}\n')
  for (const a of acoes) {
    assert.match(menu, new RegExp(`\\n  ${a}: \\{ rotulo: '`), `rótulo da ação ${a}`)
    assert.match(scriptLista, new RegExp(`acao === '${a}'`), `escolher() trata ${a}`)
  }
  assert.match(menu, /emitir: \{ rotulo: 'emitir a NF \(robô\)'/)
  assert.match(menu, /cancelar_conferido: \{ rotulo: 'cancelar \(conferi no Bling 44\)'/)
  // rotas do plano (§3.4)
  assert.match(scriptLista, /\/api\/nfse\/compras\/\$\{c\.id\}\/emitir/)
  assert.match(scriptLista, /\/api\/nfse\/compras\/\$\{c\.id\}\/tirar-da-fila/)
  assert.match(scriptLista, /\/api\/nfse\/compras\/\$\{c\.id\}\/parar-robo/)
  assert.match(scriptLista, /baixarArquivo\(`\/api\/nfse\/compras\/\$\{c\.id\}\/xml`, `nfe_/)
  // imprimir a DANFE: abre numa aba (abrirArquivo, sem await antes); baixar PDF: a mesma rota
  assert.match(menu, /imprimir: \{ rotulo: 'imprimir a NF \(DANFE\)'/)
  assert.match(menu, /pdf: \{ rotulo: 'baixar PDF \(DANFE\)'/)
  const imprimir = bloco("} else if (acao === 'imprimir')", "} else if (acao === 'pdf')")
  assert.match(imprimir, /abrirArquivo\(`\/api\/nfse\/compras\/\$\{c\.id\}\/pdf`/)
  assert.match(imprimir, /nome: nomeDanfe\(c\)/)
  assert.ok(!/await (?!rodar|abrirArquivo)/.test(imprimir.replace(/\/\/.*$/gm, '')), 'nada espera antes de abrir a aba')
  assert.match(bloco("} else if (acao === 'pdf')", "} else if (acao === 'chave')"), /baixarArquivo\(`\/api\/nfse\/compras\/\$\{c\.id\}\/pdf`, nomeDanfe\(c\)\)/)
  // a barra de baixo (como em Notas emitidas), só com NF marcada
  assert.match(tplLista, /<CompraNfBarra\s+:marcadas="marcadasNf"\s+:acao="acaoLote"\s+@imprimir="acaoArquivos\('imprimir'\)"\s+@baixar="acaoArquivos"\s+@limpar="limparSelecao"/)
  assert.match(tplLista, /:disabled="\(!podeMarcar\(c\) && !marcadas\.has\(c\.id\)\) \|\| !!acaoLote"/, 'a caixinha da linha segue o grupo')
  assert.match(tplLista, /v-if="tela\.canEdit\.value && grupoMarcado !== 'nf'"/, 'planilha das marcadas some com NF marcada')
  assert.equal((scriptLista.match(/query: \{ conferido: 1 \}/g) || []).length, 2, 'cancelar e desfazer da NF do robô mandam conferido=1')
  assert.match(scriptLista, /digitar: 'PARAR'/, 'tirar do robô digitando PARAR')
  // depois de tirar do robô, o aviso usa a etapa que o SERVIDOR devolveu (não a da última leitura)
  assert.match(bloco("acao === 'parar_robo'", "acao === 'xml'"), /rotuloEtapa\(r\)[\s\S]*passouDaSefaz\(r\.emissao_etapa\)/)
  // anotar a NF de compra que passou pelo robô leva a chave (preenchida do erro)
  assert.match(scriptLista, /chaveDoErro\(c\.emissao_erro\)/)
  assert.match(scriptLista, /body: chave \? \{ nf_numero: n, nf_chave: chave \} : \{ nf_numero: n \}/)
  assert.match(tplLista, /<template v-if="nfComChave">/)
  // filtro "Com erro"
  assert.match(scriptLista, /\{ id: 'erro', rotulo: 'Com erro' \}/)
  assert.match(scriptLista, /\['registrada', 'erro', 'nf_emitida', 'cancelada'\]\.includes\(v\)/, 'o filtro aceita erro')
  // célula Situação
  assert.match(tplLista, /pedida \{\{ quando\(c\.emissao_pedida_em\) \}\}/)
  assert.match(tplLista, /tentativa \{\{ c\.emissao_tentativas/)
  assert.match(tplLista, /tentativa \{\{ c\.emissao_tentativas \}\} de 3/, 'na fila de novo: quantas já foram')
  assert.match(tplLista, /chaveCurta\(c\.nf_chave\)/)
  assert.match(tplLista, /<NfseDica v-if="c\.emissao_erro" :texto="c\.emissao_erro"/, 'erro longo na dica')
  assert.match(tplLista, /ROTULO_STATUS\[c\.status\]\?\.classe \?\? 'pill-muted'/, 'status desconhecido não derruba a lista')
  // a releitura não segura o cadeado da página
  assert.ok(!/algoOcupado/.test(scriptLista.replace(/\/\/.*$/gm, '')), 'fora do algoOcupado')
}

// --- Emitir: EMITIR digitado + 2ª confirmação com a flag no 409
async function testarEmitir() {
  const js = transpilar(bloco('async function emitir', 'async function copiarChave'))
  const fabricar = (respostasApi, respostasConfirmar) => {
    const posts = []
    const perguntas = []
    const toasts = []
    const trocadas = []
    const erroHttp = (status, detail) => Object.assign(new Error('http'), { status, data: { detail } })
    const env = {
      tela: { confirmar: async (o) => { perguntas.push(o); return respostasConfirmar.shift() } },
      api: async (url, opts) => {
        posts.push({ url, opts: JSON.parse(JSON.stringify(opts)) })
        const r = respostasApi.shift()
        if (r instanceof Error) throw r
        return r
      },
      trocar: (c) => trocadas.push(c),
      toasts: { success: (t, l) => toasts.push(['success', t, l]), error: (t, l) => toasts.push(['error', t, l]) },
      // como o rodar() da tela: o erro que escapa vira toast de erro
      rodar: async (_c, fazer, titulo) => { try { await fazer() } catch (e) { toasts.push(['error', titulo, e.message]) } },
      codigoErro: (e) => e?.data?.detail?.code ?? null,
      erroApi: (e) => e?.data?.detail?.mensagem ?? 'erro',
      problemasApi: (e) => e?.data?.detail?.problemas ?? [],
      resumoDaEmissao: () => 'VITA compra 121 × Embalagem de COMERCIAL BEZERRA · R$ 1.125,30 · CFOP 5102',
      fmtMes: (m) => m,
    }
    const nomes = Object.keys(env)
    const emitir = new Function(...nomes, js + '\nreturn emitir')(...nomes.map((n) => env[n]))
    return { emitir, posts, perguntas, toasts, trocadas, erroHttp }
  }
  const COMPRA = {
    id: 'c1', numero: 8000001, vendedor_nome: 'COMERCIAL BEZERRA', competencia: '2026-10-01',
    comprador: { apelido: 'VITA', razao_social: 'VITA ENXOVAIS LTDA' }, total: '1125.30', cfop: '5102',
  }
  const NA_FILA = { ...COMPRA, status: 'na_fila' }

  // desistiu na 1ª pergunta: nada vai ao servidor
  {
    const t = fabricar([], [false])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 0)
    const p = t.perguntas[0]
    assert.equal(p.titulo, 'Emitir NF de verdade')
    assert.equal(p.digitar, 'EMITIR', 'trava de produção: digitar EMITIR')
    assert.equal(p.tom, 'perigo')
    assert.equal(p.botao, 'emitir')
    assert.ok(p.linhas.some((l) => /VITA compra 121 × Embalagem de COMERCIAL BEZERRA/.test(l)), 'resumo na confirmação')
  }
  // caminho feliz: um POST, sem flags
  {
    const t = fabricar([NA_FILA], [true])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 1)
    assert.equal(t.posts[0].url, '/api/nfse/compras/c1/emitir')
    assert.equal(t.posts[0].opts.method, 'POST')
    assert.deepEqual(t.posts[0].opts.body, { confirmo_planilha_nao_importada: false, confirmo_outra_no_mes: false })
    assert.deepEqual(t.trocadas, [NA_FILA])
    assert.equal(t.toasts[0][0], 'success')
  }
  // planilha já baixada → pergunta → reenvia com a flag; depois outra no mês → pergunta → as duas flags
  {
    const t0 = fabricar([], [])
    const e1 = t0.erroHttp(409, { code: 'planilha_ja_baixada', mensagem: 'baixada em 03/11 10:00' })
    const e2 = t0.erroHttp(409, { code: 'outra_no_mes', mensagem: 'compra nº 8000002 na fila' })
    const t = fabricar([e1, e2, NA_FILA], [true, true, true])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 3)
    assert.deepEqual(t.posts.map((p) => p.opts.body), [
      { confirmo_planilha_nao_importada: false, confirmo_outra_no_mes: false },
      { confirmo_planilha_nao_importada: true, confirmo_outra_no_mes: false },
      { confirmo_planilha_nao_importada: true, confirmo_outra_no_mes: true },
    ])
    assert.equal(t.perguntas.length, 3)
    assert.match(t.perguntas[1].titulo, /planilha/)
    assert.ok(t.perguntas[1].linhas.includes('baixada em 03/11 10:00'), 'a mensagem do servidor aparece')
    assert.match(t.perguntas[2].titulo, /outra compra/)
    assert.deepEqual(t.trocadas, [NA_FILA])
  }
  // disse "não" na 2ª pergunta: não reenvia
  {
    const t0 = fabricar([], [])
    const t = fabricar([t0.erroHttp(409, { code: 'planilha_ja_baixada', mensagem: 'x' })], [true, false])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 1)
    assert.equal(t.trocadas.length, 0)
    assert.equal(t.toasts.length, 0)
  }
  // o mesmo 409 de novo, já com a flag: não fica em laço, vira erro
  {
    const t0 = fabricar([], [])
    const e = t0.erroHttp(409, { code: 'planilha_ja_baixada', mensagem: 'x' })
    const t = fabricar([e, e], [true, true])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 2)
    assert.equal(t.toasts.at(-1)[0], 'error')
  }
  // 422 nao_emite_automatico: lista os problemas, sem 2ª pergunta
  {
    const t0 = fabricar([], [])
    const e = t0.erroHttp(422, { code: 'nao_emite_automatico', mensagem: 'x', problemas: ['IE de quem compra vazia', 'CFOP 6102 dentro de SP'] })
    const t = fabricar([e], [true])
    await t.emitir(COMPRA)
    assert.equal(t.posts.length, 1)
    assert.deepEqual(t.toasts, [['error', 'Essa compra não sai pelo robô', ['IE de quem compra vazia', 'CFOP 6102 dentro de SP']]])
  }
}

// --- releitura dos 30 s: só com robô, só com a aba ativa
async function testarReleitura() {
  const js = transpilar(bloco('const PASSO_MS', '// --- marcar várias'))
  let proxId = 0
  const timers = new Map()
  const fakeSet = (fn, ms) => { const id = ++proxId; timers.set(id, { fn, ms }); return id }
  const fakeClear = (id) => { timers.delete(id) }
  const rodarTimers = async () => {
    const lista = [...timers.entries()]
    for (const [id, t] of lista) { timers.delete(id); await t.fn() }
  }
  const ganchos = {}
  const compras = ref([])
  const ocupado = ref(null)
  const nfAberta = ref(null)
  const cargas = []
  const carregar = async (silencioso) => { cargas.push(silencioso) }
  const env = {
    computed, watch, ref, compras, ocupado, nfAberta, carregar,
    onActivated: (f) => { ganchos.ativou = f },
    onDeactivated: (f) => { ganchos.desativou = f },
    onBeforeUnmount: (f) => { ganchos.desmontou = f },
    setTimeout: fakeSet,
    clearTimeout: fakeClear,
  }
  const nomes = Object.keys(env)
  const s = new Function(...nomes, js + '\nreturn { PASSO_MS }')(...nomes.map((n) => env[n]))
  assert.equal(s.PASSO_MS, 30000, '30 s')
  assert.equal(timers.size, 0, 'lista vazia: nada agendado')

  compras.value = [{ id: 'a', status: 'registrada' }, { id: 'b', status: 'nf_emitida' }]
  await nextTick()
  assert.equal(timers.size, 0, 'sem compra na fila: sem releitura')

  compras.value = [{ id: 'a', status: 'na_fila' }]
  await nextTick()
  assert.equal(timers.size, 1, 'com compra na fila: agenda')
  assert.equal([...timers.values()][0].ms, 30000)
  await rodarTimers()
  assert.deepEqual(cargas, [true], 'releu em silêncio (sem esqueleto)')
  assert.equal(timers.size, 1, 'e agenda a próxima')

  ocupado.value = 'a'
  await rodarTimers()
  assert.deepEqual(cargas, [true], 'ação no meio: pula a volta')
  assert.equal(timers.size, 1, 'mas continua agendado')
  ocupado.value = null

  // saiu da aba (KeepAlive): para; voltou: relê na hora e volta a agendar
  ganchos.desativou()
  assert.equal(timers.size, 0, 'fora da aba: sem timer')
  // a lista muda escondida (ex.: resposta que chegou depois): continua parada
  compras.value = [{ id: 'a', status: 'registrada' }]
  await nextTick()
  compras.value = [{ id: 'a', status: 'emitindo' }]
  await nextTick()
  assert.equal(timers.size, 0, 'fora da aba: não volta a agendar')
  ganchos.ativou()
  assert.deepEqual(cargas, [true, true], 'voltou: relê na hora')
  assert.equal(timers.size, 1)

  // o robô terminou: para sozinho
  compras.value = [{ id: 'a', status: 'nf_emitida' }]
  await nextTick()
  assert.equal(timers.size, 0, 'sem fila: para')

  compras.value = [{ id: 'a', status: 'emitindo' }]
  await nextTick()
  assert.equal(timers.size, 1, 'emitindo também relê')
  ganchos.desmontou()
  assert.equal(timers.size, 0, 'desmontou: para')
}

// --- marcar várias: planilha (sem NF) OU imprimir/baixar (NF com chave), nunca as duas
async function testarMarcacao() {
  const js = transpilar(bloco('// --- marcar várias', '// --- ações de uma compra'))
  const chamadas = []
  const toasts = []
  const env = {
    computed, ref,
    compras: ref([]),
    marcadas: ref(new Set()),
    status: ref('todas'),
    ocupado: ref(null),
    acaoLote: ref(null),
    grupoDaMarcacao: L.grupoDaMarcacao,
    paraLote: L.paraLote,
    LOTE_COMPRA: L.LOTE_COMPRA,
    nomeDaPlanilha: L.nomeDaPlanilha,
    plural: (n, a, b) => `${n} ${n === 1 ? a : b}`,
    imprimirLote: async (notas, origem) => { chamadas.push(['imprimir', notas, origem]); return null },
    baixarLote: async (notas, tipo, origem) => { chamadas.push(['baixar', notas, tipo, origem]); return null },
    baixarArquivo: async (url, nome, opts) => { chamadas.push(['planilha', url, nome, opts]); return true },
    toasts: { warning: (t) => toasts.push(t), success: (t) => toasts.push(t) },
  }
  const nomes = Object.keys(env)
  const m = new Function(...nomes, js + `
return { listaMarcadas, grupoMarcado, grupoDoCabecalho, marcaveis, marcacao, marcadasNf, podeMarcar, motivoNaoMarca,
  limparMarcadas, alternar, marcarTodas, limparSelecao, acaoArquivos, planilhaDasMarcadas }`)(...nomes.map((n) => env[n]))
  const CH = (n) => `352611672859410001905500100000${n}1000030318`
  const comp = (id, status, extra = {}) => ({
    id, numero: Number(id.replace(/\D/g, '')) || 1, status, nf_numero: null, nf_chave: null, emissao_etapa: null, numero_queimado: false,
    vendedor_cnpj: '67285941000190', vendedor_nome: 'COMERCIAL BEZERRA', total: '10.00',
    comprador: { apelido: `cli${id}`, razao_social: 'X LTDA' }, ...extra,
  })
  const r1 = comp('r1', 'registrada')
  const r2 = comp('r2', 'registrada')
  const n1 = comp('n1', 'nf_emitida', { nf_numero: '3031', nf_chave: CH('3031') })
  const n2 = comp('n2', 'nf_emitida', { nf_numero: '3032', nf_chave: CH('3032') })
  const semChave = comp('s1', 'nf_emitida', { nf_numero: '77' })
  const fila = comp('f1', 'na_fila')
  env.compras.value = [n2, r1, semChave, n1, fila, r2]
  const ids = () => [...env.marcadas.value]

  // nada marcado: o cabeçalho em "Todas" marca as sem NF (como antes); em "NF emitida", as NFs
  assert.equal(m.grupoDoCabecalho.value, 'planilha')
  env.status.value = 'nf_emitida'
  assert.equal(m.grupoDoCabecalho.value, 'nf')
  m.marcarTodas()
  assert.deepEqual(ids(), ['n2', 'n1'], 'só as NFs com chave, na ordem da tela')
  assert.equal(m.marcacao.value, 'todas')
  assert.deepEqual(m.marcadasNf.value.map((c) => c.id), ['n2', 'n1'])
  // com NF marcada, a sem NF não marca (e diz por quê); a NF sem chave e a da fila nunca
  assert.equal(m.podeMarcar(r1), false)
  assert.match(m.motivoNaoMarca(r1), /limpe a seleção/)
  m.alternar(r1)
  assert.deepEqual(ids(), ['n2', 'n1'], 'não mistura os grupos')
  assert.equal(m.podeMarcar(semChave), false)
  assert.match(m.motivoNaoMarca(semChave), /sem a chave/)
  assert.match(m.motivoNaoMarca(fila), /robô/)
  // a planilha das marcadas não sai com NF marcada
  await m.planilhaDasMarcadas()
  assert.equal(chamadas.length, 0)

  // imprimir / baixar: as rotas da Compra, na ordem da tela, com nº e compradora para o aviso
  await m.acaoArquivos('imprimir')
  assert.deepEqual(chamadas[0][0], 'imprimir')
  assert.deepEqual(chamadas[0][1], [
    { id: 'n2', n_nfse: '3032', prestador_nome: 'clin2' },
    { id: 'n1', n_nfse: '3031', prestador_nome: 'clin1' },
  ])
  assert.equal(chamadas[0][2], L.LOTE_COMPRA)
  await m.acaoArquivos('xml')
  assert.deepEqual([chamadas[1][0], chamadas[1][2], chamadas[1][3]], ['baixar', 'xml', L.LOTE_COMPRA])
  // um lote por vez
  env.acaoLote.value = 'pdf'
  await m.acaoArquivos('imprimir')
  assert.equal(chamadas.length, 2, 'com lote em andamento não sai outro')
  m.alternar(n1)
  assert.deepEqual(ids(), ['n2', 'n1'], 'nem a seleção muda no meio do lote')
  env.acaoLote.value = null

  // a NF marcada foi desfeita (cancelada): sai da seleção na releitura
  env.compras.value = env.compras.value.map((c) => (c.id === 'n2' ? { ...c, status: 'cancelada' } : c))
  m.limparMarcadas()
  assert.deepEqual(ids(), ['n1'])
  m.limparSelecao()
  assert.equal(m.grupoMarcado.value, null)

  // sem NF: marca para a planilha; a NF fica apagada
  env.status.value = 'todas'
  m.alternar(r1)
  assert.equal(m.grupoMarcado.value, 'planilha')
  assert.equal(m.podeMarcar(n1), false)
  assert.equal(m.marcadasNf.value.length, 0, 'barra de NF escondida')
  m.marcarTodas()
  assert.deepEqual(ids(), ['r1', 'r2'], 'o cabeçalho completa o grupo marcado')
  // anotaram a NF (com chave) de uma marcada para a planilha: ela SAI, não vira NF marcada
  env.compras.value = env.compras.value.map((c) => (c.id === 'r1' ? { ...c, status: 'nf_emitida', nf_numero: '3040', nf_chave: CH('3040') } : c))
  m.limparMarcadas()
  assert.deepEqual(ids(), ['r2'])
  assert.equal(m.grupoMarcado.value, 'planilha')
  await m.planilhaDasMarcadas()
  assert.equal(chamadas.at(-1)[0], 'planilha')
  assert.deepEqual(chamadas.at(-1)[3].body, { ids: ['r2'] })
  await m.acaoArquivos('imprimir')
  assert.equal(chamadas.at(-1)[0], 'planilha', 'imprimir não sai com sem NF marcada')
}

// ---------------------------------------------------------------- CompraNfBarra.vue + a página (lote)
{
  const arqBarra = path.resolve(__dirname, '../components/CompraNfBarra.vue')
  const barra = parse(fs.readFileSync(arqBarra, 'utf8'), { filename: arqBarra })
  assert.deepEqual(barra.errors, [])
  assert.deepEqual(compileTemplate({ source: barra.descriptor.template.content, filename: arqBarra, id: 'compra-nf-barra' }).errors, [])
  const sb = barra.descriptor.scriptSetup.content
  for (const [id, rotulo] of [['imprimir', 'Imprimir'], ['pdf', 'Baixar PDFs \\(\\.zip\\)'], ['xml', 'Baixar XMLs \\(\\.zip\\)']]) {
    assert.match(sb, new RegExp(`id: '${id}' as const, rotulo: '${rotulo}'`), `botão ${id}`)
  }
  assert.match(sb, /n\.value > MAX_LOTE_ARQUIVOS/, 'no máximo 100 por vez')
  assert.match(barra.descriptor.template.content, /<div v-if="n > 0"/, 'só aparece com NF marcada')

  // a página: o lote da NFS-e continua igual (padrão LOTE_NFSE) e o da Compra só troca rota e textos
  const arqPagina = path.resolve(__dirname, '../pages/emissao-servico.vue')
  const pagina = fs.readFileSync(arqPagina, 'utf8')
  assert.match(pagina, /async function imprimirLote\(notas: NotaDoLote\[\], o: OrigemLote = LOTE_NFSE\)/)
  assert.match(pagina, /o: OrigemLote = LOTE_NFSE,\n\): Promise<ResultadoArquivos \| null>/, 'baixarLote com padrão NFS-e')
  assert.match(pagina, /apiN<Blob>\(`\$\{o\.rota\}\/imprimir`/)
  assert.match(pagina, /apiN<Blob>\(`\$\{o\.rota\}\/arquivos`/)
  assert.ok(!/'\/api\/nfse\/emissoes\/lote\//.test(pagina), 'sem rota de lote fixa na página')
  // a DANFE de uma compra: aba aberta no clique, PDF com a chave no cabeçalho
  const abrir = pagina.slice(pagina.indexOf('async function abrirArquivo'), pagina.indexOf('const nfseApi'))
  assert.ok(abrir.indexOf('abrirAbaJa(') >= 0 && abrir.indexOf('abrirAbaJa(') < abrir.indexOf('await'), 'a aba abre antes de esperar')
  assert.match(abrir, /apiN<Blob>\(path, \{ responseType: 'blob' \}\)/)
  assert.match(pagina, /const nfseApi: NfseApi = \{[^}]*abrirArquivo[^}]*\}/)
}

// ---------------------------------------------------------------- CompraForm.vue + ContabilidadeCompra.vue
{
  const arqForm = path.resolve(__dirname, '../components/CompraForm.vue')
  const form = parse(fs.readFileSync(arqForm, 'utf8'), { filename: arqForm })
  assert.deepEqual(form.errors, [])
  assert.deepEqual(compileTemplate({ source: form.descriptor.template.content, filename: arqForm, id: 'compra-form' }).errors, [])
  const tpl = form.descriptor.template.content
  assert.match(tpl, /vendedoraAutomatica\(v\) \? ' · automático' : ''/, 'a opção da vendedora diz automático')
  assert.match(tpl, /Vendedora automática: emita pelo robô em Compras feitas/)

  const arqCc = path.resolve(__dirname, '../components/ContabilidadeCompra.vue')
  const cc = fs.readFileSync(arqCc, 'utf8')
  assert.match(cc, /<CompraLista[\s\S]*?:vendedoras="vendedoras"/, 'a lista recebe as vendedoras (quem é automática)')
}

;(async () => {
  await testarEmitir()
  await testarReleitura()
  await testarMarcacao()
  console.log('PASS: 6 status e 9 etapas iguais ao servidor; menu por status (queimado sem planilha/editar/reabrir/excluir/emitir, manual sem emitir); Emitir com EMITIR e reenvio com flag no 409; releitura de 30 s só com robô e com a aba ativa; vendedora automática no formulário; imprimir/baixar a DANFE da NF emitida com chave (menu, caixinhas sem misturar com a planilha, lote pelas rotas da Compra)')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
