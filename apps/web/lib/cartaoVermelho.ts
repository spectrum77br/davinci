// Cartão vermelho da Tabela de Preços (M-3, 17/11/2026) — a regra num lugar só.
//
// M-22 (24/11/2026): a coluna Pendências da aba Faturamento conta os cartões
// vermelhos de cada conta "pela MESMA regra do cartão da grade". Para não
// existir uma cópia que um dia diverge, a regra saiu da página
// (pages/pricing/[tab].vue) e mora aqui: a grade e o Faturamento
// (components/FaturamentoPainel.vue) chamam ESTAS funções. O código foi movido
// sem mudar uma linha da regra; tests/pricing-cartao-compartilhado.cjs compara
// com a cópia congelada de antes da M-22.
//
// Sem import aqui: os testes rodam este arquivo solto.

// ---------- Conferência dos anúncios (02/11/2026)
// O servidor lê o preço VIVO de cada anúncio e guarda; a célula ganha (!)
// quando algum anúncio dela foge da margem de erro em relação ao preço da
// Tabela, e o cartão vermelho junta esse e os outros problemas da célula.
export type AnuncioConferido = {
  anuncio: string
  variacao: string | null
  preco: number | null
  preco_promocional: number | null
  ativo: boolean | null
  motivo: string | null
}
export type CelulaConferida = {
  pricing_account_id: string
  pricing_product_id: string
  preco_tabela: number | null
  situacao: 'conferida' | 'sem_anuncio' | 'erro_conta' | 'nao_suportada'
  motivo: string | null
  conferido_em: string
  anuncios: AnuncioConferido[]
  // O job que gravou a célula (null em linha antiga, sem job, ou conferida
  // sozinha pelo clique no cartão — M-15).
  job_id: string | null
}
// Margem da conferência (M-2, 17/11/2026): no servidor e igual para todos
// (decisão do Eduardo). Uma Geral por departamento (padrão 5%) e um % próprio
// por produto — produto com desconto ganha a margem dele e para de aparecer
// como irregular. As caixas "Geral" e "Por produto" também ficam gravadas.
// `produtos`: pricing_product_id → %, só os do departamento. `pode_editar` =
// administrador (PUT /conferencia/margem e /conferencia/margem/produto/{id}).
export type MargemConferencia = {
  department: string
  margem_pct: number
  geral_ligada: boolean
  por_produto_ligada: boolean
  produtos: Record<string, number>
  pode_editar: boolean
}
export type Divergencia = {
  tabela: number
  fora: AnuncioConferido[]
  conferidoEm: string
  // Anúncios da célula sem preço lido (o balão do (!) avisa).
  naoLidos: number
  anuncios: number
  // A margem usada na linha (a do produto ou a Geral), para o balão.
  margem: number
}
// O que o cartão lê da célula do /grid (a página tem o tipo completo).
export type CatalogoAnuncioCartao = {
  external_id: string
  status: string | null
  bloqueio?: string | null
}
export type CatalogoCelulaCartao = {
  anuncios: CatalogoAnuncioCartao[]
  bloqueio: string | null
  texto: string | null
}
export type CelulaCartao = {
  pricing_account_id: string
  pricing_product_id: string
  price: string | number | null
  source: string
  cell_status: string
  catalogo?: CatalogoCelulaCartao | null
}
// A conta que diz se há integração: na coluna de catálogo, a BASE (a filha
// nunca tem integration_id).
export type ContaCartao = {
  id: string
  integration_id: string | null
  canal?: string
  conta_base_id?: string | null
}

// Sem linha no banco o servidor manda 5% com as duas caixas ligadas.
export const MARGEM_PADRAO = 5

// O % no texto do cartão: "7,5" (o mesmo fmtPct da página).
export const fmtPct = (v: number) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 })

// A margem como a tela usa: números de verdade (o NUMERIC pode vir como texto)
// e o padrão do servidor no que faltar. Servidor sem o campo: 5%, as duas
// caixas ligadas e ninguém edita.
export function margemDaResposta(bruta: Partial<MargemConferencia> | null | undefined, dep: string): MargemConferencia {
  const produtos: Record<string, number> = {}
  for (const [id, v] of Object.entries(bruta?.produtos ?? {})) {
    const n = Number(v)
    if (v != null && Number.isFinite(n)) produtos[id] = n
  }
  const pct = Number(bruta?.margem_pct)
  return {
    department: bruta?.department ?? dep,
    margem_pct: bruta?.margem_pct != null && Number.isFinite(pct) ? pct : MARGEM_PADRAO,
    geral_ligada: bruta?.geral_ligada ?? true,
    por_produto_ligada: bruta?.por_produto_ligada ?? true,
    produtos,
    pode_editar: bruta?.pode_editar ?? false,
  }
}

// A margem que vale para a linha: o % próprio do produto (com "Por produto"
// ligado), senão a Geral (com "Geral" ligado), senão nenhuma — a linha não é
// conferida (sem (!) e sem o motivo "fora da margem" no cartão vermelho).
export function margemDaLinha(m: MargemConferencia | null | undefined, pricingProductId: string): number | null {
  if (!m) return null
  if (m.por_produto_ligada) {
    const propria = m.produtos?.[pricingProductId]
    if (propria != null && Number.isFinite(Number(propria))) return Number(propria)
  }
  if (!m.geral_ligada) return null
  const geral = Number(m.margem_pct)
  return Number.isFinite(geral) ? geral : null
}

// Anúncios cuja diferença para o preço da Tabela passa da margem (em %). Na
// margem exata não marca; a folga de 1e-9 evita (!) por arredondamento.
export function anunciosForaDaMargem(
  anuncios: AnuncioConferido[],
  tabela: number,
  margemPct: number,
): AnuncioConferido[] {
  if (!(tabela > 0)) return []
  return anuncios.filter(
    (a) => a.preco != null && (Math.abs(a.preco - tabela) / tabela) * 100 > margemPct + 1e-9,
  )
}

// Célula que a grade mostra com preço que vai para o marketplace (NA/SV/
// desligada ou sem preço não tem o que comparar).
export function celulaComparavel<C extends CelulaCartao>(c: C | null | undefined): C | null {
  if (!c || c.price == null || c.cell_status === 'NA' || c.cell_status === 'SV' || c.source === 'disabled') {
    return null
  }
  return c
}

// ---------- Anúncio à venda (M-31, 24/11/2026)
// Só anúncio À VENDA conta para comparar preço, acender o cartão vermelho e
// mostrar o (!). Não está à venda: o leitor disse que não está ativo
// (rascunho, pausado, em revisão, reprovado, excluído, vínculo morto...), ou
// veio sem preço porque foi encerrado/excluído/moderado. `ativo` vazio (o
// leitor não sabe) conta como à venda.
// Texto do leitor que é anúncio fora de venda (ML encerrado/moderado,
// Shopee/TikTok excluído, TikTok rascunho/reprovado/congelado/desativado,
// vínculo morto "não existe mais"/"bloqueado").
export const PADRAO_ANUNCIO_FORA_DE_VENDA = /pausad|em revis|encerrad|exclu[íi]d|modera|rascunho|reprovad|removid|banid|bloquead|congelad|desativad|n[ãa]o existe mais/i

export function anuncioAVenda(a: AnuncioConferido): boolean {
  if (a.ativo === false) return false
  return !(a.preco == null && a.motivo != null && PADRAO_ANUNCIO_FORA_DE_VENDA.test(a.motivo))
}

// A situação do anúncio fora de venda, para o balão do cartão.
export function situacaoForaDeVenda(a: AnuncioConferido): string {
  const variacao = a.variacao ? ` (variação ${a.variacao})` : ''
  const detalhe = a.motivo && PADRAO_ANUNCIO_FORA_DE_VENDA.test(a.motivo)
    ? a.motivo.replace(/\s*—\s*o envio pula$/, '')
    : 'inativo no marketplace (pausado, em revisão ou fora do ar)'
  return `Anúncio ${a.anuncio}${variacao}: ${detalhe}`
}

// Compara com o preço ATUAL da célula (o da Tabela agora), não com o da hora
// da conferência, e com a margem da linha. Calculado uma vez para a grade
// inteira (a célula só consulta o mapa). Chave: `produto::conta`. Só os
// anúncios à venda (M-31).
export function divergenciasDaConferencia(
  celulas: readonly CelulaConferida[],
  mg: MargemConferencia | null | undefined,
  celulaDe: (prodId: string, accId: string) => CelulaCartao | null | undefined,
): Map<string, Divergencia> {
  const m = new Map<string, Divergencia>()
  if (!mg) return m
  for (const conf of celulas) {
    if (conf.situacao !== 'conferida') continue
    const margemLinha = margemDaLinha(mg, conf.pricing_product_id)
    if (margemLinha == null) continue
    const c = celulaComparavel(celulaDe(conf.pricing_product_id, conf.pricing_account_id))
    if (!c) continue
    const tabela = Number(c.price)
    const aVenda = conf.anuncios.filter(anuncioAVenda)
    const fora = anunciosForaDaMargem(aVenda, tabela, margemLinha)
    if (fora.length) {
      m.set(`${conf.pricing_product_id}::${conf.pricing_account_id}`, {
        tabela,
        fora,
        conferidoEm: conf.conferido_em,
        naoLidos: aVenda.filter((a) => a.preco == null).length,
        anuncios: aVenda.length,
        margem: margemLinha,
      })
    }
  }
  return m
}

// "Sem anúncio": o servidor grava um texto (situacao 'sem_anuncio' da
// conferência e `texto` do bloqueio do catálogo no /grid) — MOTIVO_SEM_INTEGRACAO
// e MOTIVO_SEM_VINCULO (services/pricing/conferencia.py) e os do catálogo
// (TEXTO_BLOQUEIO e o da conta de kit sem integração, services/pricing/
// anuncios.py). Outro texto é anúncio que o envio pula ('fora_do_envio':
// pausado, em revisão, sincronizado, só catálogo...).
export type TipoSemAnuncio = 'sem_integracao' | 'sem_anuncio' | 'catalogo_nao_lido' | 'fora_do_envio'
export const MOTIVO_SEM_INTEGRACAO = 'conta sem integração'
export const MOTIVO_SEM_VINCULO = 'sem anúncio vinculado'
export const PREFIXOS_SEM_INTEGRACAO = ['Conta de kit sem integração']
export const PREFIXOS_SEM_VINCULO = ['Sem anúncio de catálogo vinculado']
export const PREFIXOS_CATALOGO_NAO_LIDO = ['Marca de catálogo ainda não lida']

export function tipoSemAnuncio(motivo: string | null): TipoSemAnuncio {
  const m = motivo ?? ''
  if (m === MOTIVO_SEM_INTEGRACAO || PREFIXOS_SEM_INTEGRACAO.some((p) => m.startsWith(p))) return 'sem_integracao'
  if (!m || m === MOTIVO_SEM_VINCULO || PREFIXOS_SEM_VINCULO.some((p) => m.startsWith(p))) return 'sem_anuncio'
  if (PREFIXOS_CATALOGO_NAO_LIDO.some((p) => m.startsWith(p))) return 'catalogo_nao_lido'
  return 'fora_do_envio'
}

// Motivo curto por anúncio (campo `bloqueio` de cada anúncio do /grid).
export const MOTIVO_ANUNCIO: Record<string, string> = {
  mesmo_anuncio: 'outra linha também manda preço',
  sincronizado: 'sincronizado',
  pausado: 'pausado',
  em_revisao: 'em revisão',
  encerrado: 'encerrado',
  so_catalogo: 'vai pela coluna Catálogo',
}

// ---------- Cartão vermelho (M-3, 17/11/2026)
// Retângulo vermelho pequeno, à esquerda do aviãozinho, quando a célula tem
// algum destes problemas (o balão lista os motivos):
//   a) a conta não tem integração no DaVinci (na coluna de catálogo, a conta
//      de kit dela) — token vencido não conta;
//   b) não há anúncio vinculado (não repete quando já é o a);
//   c) há anúncio de catálogo vinculado inativo (pausado, em revisão,
//      encerrado) — basta um, e o balão diz qual; na conferência (M-31),
//      só quando NENHUM anúncio da célula está à venda ("Nenhum anúncio à
//      venda nesta conta", com a situação de cada um embaixo) — com algum à
//      venda, os outros não acendem nada;
//   d) o último envio de preço deu erro;
//   e) o preço do anúncio está fora da margem (a do produto ou a Geral).
// Vem do /grid (integração, cell_status, bloqueio do catálogo) e da última
// conferência (anúncios lidos ao vivo; "sem anúncio vinculado" na coluna de
// kit). Bloqueio do catálogo que é regra do envio (sincronizado, mesmo
// anúncio, sem tipo, sem preço de catálogo, só catálogo, marca ainda não
// lida) não é defeito: sem cartão.
export type EntradaCartao = {
  // A conta que diz se há integração: na coluna de catálogo, a BASE (a
  // filha nunca tem integration_id).
  conta: { integration_id: string | null } | null | undefined
  celula: Pick<CelulaCartao, 'cell_status' | 'catalogo'> | null | undefined
  conferida: CelulaConferida | null | undefined
  divergencia: Divergencia | null | undefined
}
export const CARTAO_SEM_INTEGRACAO = 'Conta sem integração no DaVinci'
export const CARTAO_SEM_ANUNCIO = 'Sem anúncio vinculado nesta conta'
export const CARTAO_ERRO_ENVIO = 'O último envio de preço deu erro'
// M-31: a célula tem anúncio vinculado, mas nenhum à venda.
export const CARTAO_NENHUM_A_VENDA = 'Nenhum anúncio à venda nesta conta'
// Anúncios inativos listados no balão; o resto vira "e mais N".
export const CARTAO_INATIVOS_NO_BALAO = 3
export const BLOQUEIOS_ANUNCIO_INATIVO = new Set(['pausado', 'em_revisao', 'encerrado'])
// Status gravado do anúncio de catálogo → bloqueio (o _MOTIVO_POR_STATUS de
// services/pricing/anuncios.py; 'inactive' é anúncio parado pelo ML).
export const BLOQUEIO_POR_STATUS: Record<string, string> = {
  paused: 'pausado',
  inactive: 'pausado',
  under_review: 'em_revisao',
  closed: 'encerrado',
}
// Texto da conferência que é anúncio inativo (e não regra do envio): ML
// encerrado/moderado, Shopee/TikTok excluído, catálogo pausado/em revisão.
export const PADRAO_ANUNCIO_INATIVO = /pausad|em revis|encerrad|exclu[íi]d|modera/i

export function motivosDoCartao(e: EntradaCartao): string[] {
  const motivos: string[] = []
  const somar = (m: string) => {
    if (!motivos.includes(m)) motivos.push(m)
  }
  const cat = e.celula?.catalogo ?? null
  const conf = e.conferida ?? null

  // a) Conta sem integração (inclusive a coluna de catálogo de uma conta de
  // kit sem integração).
  const semIntegracao = (!!e.conta && e.conta.integration_id == null)
    || (cat?.bloqueio === 'sem_anuncio' && tipoSemAnuncio(cat.texto) === 'sem_integracao')
  if (semIntegracao) somar(CARTAO_SEM_INTEGRACAO)

  // b) Sem anúncio vinculado. Célula com `catalogo` (coluna de catálogo, ou
  // de kit que só tem anúncio de catálogo): quem diz é o /grid. Coluna de kit:
  // a conferência, ou o envio que gravou no_link.
  if (!semIntegracao) {
    const semVinculo = e.celula?.cell_status === 'no_link' || (cat
      ? cat.bloqueio === 'sem_anuncio' && tipoSemAnuncio(cat.texto) === 'sem_anuncio'
      : conf?.situacao === 'sem_anuncio' && tipoSemAnuncio(conf.motivo) === 'sem_anuncio')
    if (semVinculo) somar(CARTAO_SEM_ANUNCIO)
  }

  // c) Anúncio vinculado inativo.
  const inativos: string[] = []
  for (const an of cat?.anuncios ?? []) {
    // 'so_catalogo': o anúncio é da coluna Catálogo, não desta.
    if (an.bloqueio === 'so_catalogo') continue
    const bloqueio = an.bloqueio && BLOQUEIOS_ANUNCIO_INATIVO.has(an.bloqueio)
      ? an.bloqueio
      : BLOQUEIO_POR_STATUS[(an.status ?? '').toLowerCase()]
    if (bloqueio) inativos.push(`Anúncio de catálogo ${an.external_id} ${MOTIVO_ANUNCIO[bloqueio] ?? bloqueio}`)
  }
  if (!inativos.length && cat?.bloqueio && BLOQUEIOS_ANUNCIO_INATIVO.has(cat.bloqueio)) {
    inativos.push(`Anúncio de catálogo ${MOTIVO_ANUNCIO[cat.bloqueio] ?? cat.bloqueio}`)
  }
  // "Fora do envio" da conferência: só pausado/em revisão/encerrado é motivo
  // (sincronizado, mesmo anúncio, só catálogo... são regra do envio).
  if (!cat && conf?.situacao === 'sem_anuncio' && tipoSemAnuncio(conf.motivo) === 'fora_do_envio'
    && PADRAO_ANUNCIO_INATIVO.test(conf.motivo ?? '')) {
    inativos.push(conf.motivo!)
  }
  for (const m of inativos.slice(0, CARTAO_INATIVOS_NO_BALAO)) somar(m)
  if (inativos.length > CARTAO_INATIVOS_NO_BALAO) {
    somar(`… e mais ${inativos.length - CARTAO_INATIVOS_NO_BALAO} anúncio(s) inativo(s)`)
  }
  // O que a conferência leu ao vivo (M-31): com algum anúncio à venda, os
  // outros (rascunho, pausado, excluído...) não acendem nada — o cartão e o
  // (!) dependem só dos à venda. Sem nenhum à venda: um motivo só, e embaixo
  // cada anúncio com a situação dele.
  const lidos = conf?.anuncios ?? []
  if (lidos.length && !lidos.some(anuncioAVenda)) {
    somar(CARTAO_NENHUM_A_VENDA)
    for (const a of lidos.slice(0, CARTAO_INATIVOS_NO_BALAO)) somar(situacaoForaDeVenda(a))
    if (lidos.length > CARTAO_INATIVOS_NO_BALAO) {
      somar(`… e mais ${lidos.length - CARTAO_INATIVOS_NO_BALAO} anúncio(s) fora de venda`)
    }
  }

  // d) O último envio deu erro (o texto do erro não fica guardado).
  if (e.celula?.cell_status === 'error') somar(CARTAO_ERRO_ENVIO)

  // e) Fora da margem da linha.
  const d = e.divergencia
  if (d && d.fora.length) {
    const a = d.fora[0]
    const dif = ((a.preco! - d.tabela) / d.tabela) * 100
    const mais = d.fora.length > 1 ? ` (e mais ${d.fora.length - 1})` : ''
    somar(
      `Preço do anúncio fora da margem de ${fmtPct(d.margem)}%: anúncio ${Math.round(a.preco!)} × Tabela ${Math.round(d.tabela)}`
      + ` (${dif > 0 ? '+' : ''}${dif.toFixed(1).replace('.', ',')}%)${mais}`,
    )
  }
  return motivos
}

// Os motivos de cada célula da grade (produto::conta), já juntos para o
// balão. Uma passada na grade inteira; vale também para célula sem preço
// (conta sem integração: cartão em todas). `contaParametros`: na coluna de
// catálogo, a conta de kit dela (quem tem a integração).
export function cartoesDaGrade(args: {
  contas: readonly ContaCartao[]
  produtos: readonly { id: string }[]
  contaParametros: (acc: ContaCartao) => { integration_id: string | null } | null | undefined
  celulaDe: (chave: string) => CelulaCartao | null | undefined
  conferidaDe: (chave: string) => CelulaConferida | null | undefined
  divergenciaDe: (chave: string) => Divergencia | null | undefined
}): Map<string, string> {
  const m = new Map<string, string>()
  for (const acc of args.contas) {
    const conta = args.contaParametros(acc)
    for (const prod of args.produtos) {
      const chave = `${prod.id}::${acc.id}`
      const motivos = motivosDoCartao({
        conta,
        celula: args.celulaDe(chave),
        conferida: args.conferidaDe(chave),
        divergencia: args.divergenciaDe(chave),
      })
      if (motivos.length) m.set(chave, motivos.join('\n'))
    }
  }
  return m
}

// ---------- Pendências do Faturamento (M-22, 24/11/2026)
// Uma categoria inteira (Celular, Eletro ou Mala), como a grade a monta: o
// /grid e a conferência do MESMO departamento. A conta → quantos cartões
// vermelhos ela tem, com as mesmas funções da grade:
// - a coluna de catálogo usa a conta de kit dela (contaParametros da página);
// - a célula com "sem anúncio vinculado" vencido sai como na grade aberta
//   (`aplicarSemVinculo`: o servidor manda as que continuam marcadas);
// - a margem é a da resposta da conferência (margemDaResposta).
export type GradeCartao = {
  accounts: readonly ContaCartao[]
  products: readonly { id: string }[]
  cells: readonly CelulaCartao[]
}
export type ConferenciaCartao = {
  celulas?: readonly CelulaConferida[] | null
  margem?: Partial<MargemConferencia> | null
  sem_vinculo?: readonly { pricing_account_id: string; pricing_product_id: string }[] | null
}

export function cartoesPorConta(
  grade: GradeCartao,
  conferencia: ConferenciaCartao | null | undefined,
  departamento: string,
): Map<string, number> {
  const ainda = conferencia?.sem_vinculo
    ? new Set(conferencia.sem_vinculo.map((c) => `${c.pricing_product_id}::${c.pricing_account_id}`))
    : null
  const celulas = new Map<string, CelulaCartao>()
  for (const c of grade.cells) {
    const chave = `${c.pricing_product_id}::${c.pricing_account_id}`
    celulas.set(chave, ainda && c.cell_status === 'no_link' && !ainda.has(chave) ? { ...c, cell_status: 'auto' } : c)
  }
  const conferidas = new Map<string, CelulaConferida>()
  for (const c of conferencia?.celulas ?? []) conferidas.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c)
  const divergencias = conferencia
    ? divergenciasDaConferencia(
      conferencia.celulas ?? [],
      margemDaResposta(conferencia.margem, departamento),
      (p, a) => celulas.get(`${p}::${a}`),
    )
    : new Map<string, Divergencia>()
  const porId = new Map(grade.accounts.map((a) => [a.id, a]))
  const cartoes = cartoesDaGrade({
    contas: grade.accounts,
    produtos: grade.products,
    contaParametros: (acc) => (acc.canal === 'catalogo' && acc.conta_base_id ? porId.get(acc.conta_base_id) ?? acc : acc),
    celulaDe: (k) => celulas.get(k),
    conferidaDe: (k) => conferidas.get(k),
    divergenciaDe: (k) => divergencias.get(k),
  })
  const porConta = new Map<string, number>()
  for (const acc of grade.accounts) porConta.set(acc.id, 0)
  for (const chave of cartoes.keys()) {
    const conta = chave.slice(chave.indexOf('::') + 2)
    porConta.set(conta, (porConta.get(conta) ?? 0) + 1)
  }
  return porConta
}
