// Marketing › Conferência Shopee (06/10/2026) — tipos do relatório congelado
// (contrato §5, versão 1) e as regras de formatação e de variação.
//
// 07/10/2026: o Resumo saiu no formato da planilha antiga do dono ("mais ou
// menos desse jeito"): métrica × (semana × Mala/Celular/Eletro/Geral) + o bloco
// Variação. Quem monta as linhas e colunas é planilhaResumo(), aqui embaixo — o
// Excel e o HTML do servidor seguem o mesmo desenho.
//
// As MESMAS regras rodam no servidor (Excel, CSV, MD, HTML e o aviso do
// Threema): "▲ 12,3%", "▼ 0,8 p.p.", "novo", "=", "—", e a cor pelo que é
// bom em cada métrica. Mudou aqui, muda lá — senão a tela e a planilha
// contam histórias diferentes do mesmo número.
//
// O teste (tests/conferencia-lib.cjs) roda este arquivo de verdade, sem nada
// em volta: nenhum import. E nenhuma classe do Tailwind — o Tailwind não varre
// lib/, então classe escrita aqui sairia sem CSS. Os helpers devolvem o
// SIGNIFICADO ('verde', 'vermelho', 'cinza') e o componente traduz pra cor.

export type TipoMetrica = 'dinheiro' | 'inteiro' | 'percentual'
export type Bom = 'sobe' | 'desce' | 'neutro'
export type ChaveMetrica =
  | 'vendas_afiliados'
  | 'vendas_ads'
  | 'saldo_ads'
  | 'impressoes'
  | 'invest_afiliados'
  | 'invest_ads'
  | 'pct'
  | 'vendas'
  // 07/10/2026: cliques, pedidos e conversão (afiliados e Ads). Relatório
  // congelado antes disso não tem essas chaves: valor() devolve null → "—".
  | 'cliques_afiliados'
  | 'pedidos_afiliados'
  | 'conversao_afiliados'
  | 'cliques_ads'
  | 'pedidos_ads'
  | 'conversao_ads'
export type ChaveGrupo = 'mala' | 'celular' | 'eletro'
/** Uma semana de uma linha (ou de um total): número, ou null = "sem dados". */
export type Valores = Partial<Record<ChaveMetrica, number | null>>

export interface Metrica {
  chave: ChaveMetrica
  rotulo: string
  tipo: TipoMetrica
  bom: Bom
}

export interface SemanaPeriodo {
  inicio: string // AAAA-MM-DD
  fim: string
  rotulo?: string // "28/09–04/10"
}

export interface LinhaConta {
  conta_id: string | null
  conta: string
  usuario: string | null
  status: string
  erro: string | null
  avisos: string[]
  semanas: Valores[] // x4, índice 0 = S1 (atual)
}

export interface TotalGrupo {
  contas: number
  sem_dados: number
  semanas: Valores[]
}

export interface GrupoRelatorio {
  chave: ChaveGrupo
  rotulo: string
  linhas: LinhaConta[]
  total: TotalGrupo
}

export interface Relatorio {
  versao: number
  execucao_id: string
  tipo: 'semanal' | 'parcial'
  origem: 'agenda' | 'manual'
  gerado_em: string
  criado_em: string
  semanas: SemanaPeriodo[]
  metricas: Metrica[]
  grupos: GrupoRelatorio[]
  geral: TotalGrupo
  notas: string[]
  contas_sem_dados: { conta: string; status: string; erro: string | null }[]
  afiliados_incompletos: { conta: string; ate: string }[]
  divergencias: { conta: string; item_id: string; nome: string; davinci: 'eletro' | 'outro'; categoria_shopee: number | null }[]
  nao_atribuido_ads: { conta: string; gasto: number }[]
}

export type StatusExecucao = 'coletando' | 'pronto' | 'cancelado'
export type StatusColeta =
  | 'pendente' | 'coletando' | 'ok' | 'parcial' | 'deslogada' | 'perfil_em_uso'
  | 'sem_automacao' | 'bloqueada' | 'interrompida' | 'erro' | 'expirada'

export interface ExecucaoResumo {
  id: string
  tipo: 'semanal' | 'parcial'
  origem: 'agenda' | 'manual'
  status: StatusExecucao
  criado_em: string
  finalizado_em: string | null
  semanas: SemanaPeriodo[]
  resumo?: { contas: number; ok: number; sem_dados: number } | null
}

export interface Execucao extends ExecucaoResumo {
  criado_por?: string | null
  afiliados_ate?: string | null
  esperar_afiliados_ate?: string | null
  corte?: string | null
  prazo?: string | null
  threema_enviado_em?: string | null
}

export interface Coleta {
  id: string
  nome: string
  grupo: string
  status: StatusColeta | string
  erro: string | null
  tentativas: number
  adiamentos: number // voltas pra fila (perfil em uso + espera dos afiliados)
  adiamentos_perfil?: number // só as de perfil em uso (essas têm limite de 3)
  concluido_em: string | null
}

export interface DetalheExecucao {
  execucao: Execucao
  coletas: Coleta[]
  relatorio: Relatorio | null
}

export interface ContaConferencia {
  id: string
  adspower_user_id: string
  nome: string
  grupo: 'mala' | 'celular'
  ativo: boolean
  ordem: number
  conta_key: string | null
  observacao: string | null
}

export type Formato = 'xlsx' | 'csv' | 'md' | 'json' | 'html'

/** Códigos de erro da API → frase pra tela. */
export const ERROS_CONFERENCIA: Record<string, string> = {
  conferencia_em_andamento: 'Já tem uma conferência coletando. Espere ela terminar ou cancele antes de gerar outra.',
  conferencia_sem_contas: 'Nenhuma loja ativa — ative ao menos uma em Contas antes de gerar.',
  conferencia_link_invalido: 'Link do Excel vencido ou inválido — baixe pela aba Conferência no DaVinci.',
  conferencia_nao_encontrada: 'Essa conferência não existe mais.',
  execucao_nao_encontrada: 'Essa conferência não existe mais.',
  conferencia_sem_relatorio: 'Essa conferência ainda não tem relatório.',
  relatorio_nao_encontrado: 'Essa conferência ainda não tem relatório.',
  conferencia_nao_pronta: 'Só dá para recalcular uma conferência que já terminou.',
  conferencia_nao_coletando: 'Essa conferência já terminou — não tem mais o que cancelar.',
  conta_nao_encontrada: 'Essa conta não existe mais.',
  forbidden: 'Sem permissão para isso (Marketing › editar).',
}

// ---------- helpers puros (travados em tests/conferencia-lib.cjs)

/**
 * As métricas, nesta ordem (contrato §5 + as 6 de cliques/pedidos/conversão de
 * 07/10/2026, no fim). O relatório traz a mesma lista; relatório antigo traz só
 * as 8 primeiras. Conversão = pedidos ÷ cliques × 100 (no total do grupo, das
 * somas — nunca a média dos percentuais).
 */
export const METRICAS: Metrica[] = [
  { chave: 'vendas_afiliados', rotulo: 'Vendas afiliados', tipo: 'dinheiro', bom: 'sobe' },
  { chave: 'vendas_ads', rotulo: 'Vendas Ads', tipo: 'dinheiro', bom: 'sobe' },
  { chave: 'saldo_ads', rotulo: 'Saldo Ads', tipo: 'dinheiro', bom: 'neutro' },
  { chave: 'impressoes', rotulo: 'Impressões', tipo: 'inteiro', bom: 'sobe' },
  { chave: 'invest_afiliados', rotulo: 'Invest. afiliados', tipo: 'dinheiro', bom: 'neutro' },
  { chave: 'invest_ads', rotulo: 'Invest. Ads', tipo: 'dinheiro', bom: 'neutro' },
  { chave: 'pct', rotulo: '% s/ vendas', tipo: 'percentual', bom: 'desce' },
  { chave: 'vendas', rotulo: 'Vendas', tipo: 'dinheiro', bom: 'sobe' },
  { chave: 'cliques_afiliados', rotulo: 'Cliques afiliados', tipo: 'inteiro', bom: 'neutro' },
  { chave: 'pedidos_afiliados', rotulo: 'Pedidos afiliados', tipo: 'inteiro', bom: 'sobe' },
  { chave: 'conversao_afiliados', rotulo: 'Conversão afiliados', tipo: 'percentual', bom: 'sobe' },
  { chave: 'cliques_ads', rotulo: 'Cliques Ads', tipo: 'inteiro', bom: 'neutro' },
  { chave: 'pedidos_ads', rotulo: 'Pedidos Ads', tipo: 'inteiro', bom: 'sobe' },
  { chave: 'conversao_ads', rotulo: 'Conversão Ads', tipo: 'percentual', bom: 'sobe' },
]
const METRICA: Record<string, Metrica> = Object.fromEntries(METRICAS.map((m) => [m.chave, m]))

const ROTULO_STATUS_COLETA: Record<string, string> = {
  pendente: 'na fila',
  coletando: 'coletando',
  ok: 'ok',
  parcial: 'parcial',
  deslogada: 'deslogada',
  perfil_em_uso: 'perfil em uso',
  sem_automacao: 'perfil Firefox, o robô não abre',
  bloqueada: 'bloqueada pela Shopee',
  interrompida: 'interrompida',
  erro: 'erro',
  expirada: 'não coletada a tempo',
}
/** "sem_automacao" → "sem automação". Status desconhecido aparece cru. */
export function rotuloStatusColeta(s: string | null | undefined): string {
  if (!s) return '—'
  return ROTULO_STATUS_COLETA[s] ?? s.replace(/_/g, ' ')
}
/** Tom do status da coleta: o componente traduz pra pill. */
export function tomStatusColeta(s: string | null | undefined): 'ok' | 'alerta' | 'erro' | 'andamento' | 'neutro' {
  if (s === 'ok') return 'ok'
  if (s === 'parcial' || s === 'perfil_em_uso' || s === 'expirada') return 'alerta'
  if (s === 'coletando') return 'andamento'
  if (s === 'pendente' || !s) return 'neutro'
  return 'erro'
}
/** Coleta que já acabou (com ou sem dados). */
export function coletaTerminou(s: string | null | undefined): boolean {
  return !!s && s !== 'pendente' && s !== 'coletando'
}

const ROTULO_STATUS_EXECUCAO: Record<string, string> = {
  coletando: 'coletando',
  pronto: 'pronto',
  cancelado: 'cancelado',
}
export function rotuloStatusExecucao(s: string | null | undefined): string {
  if (!s) return '—'
  return ROTULO_STATUS_EXECUCAO[s] ?? s
}

// ── números ─────────────────────────────────────────────────────────────

function ok(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/**
 * Dígitos de |v| com `casas` decimais, arredondados como o servidor
 * (calculo.meio_para_cima): sobre o número como ele se escreve — a forma curta
 * do String(v), a mesma do repr() do Python — e meio pra LONGE do zero
 * ("6.35" → "6,4", "6.25" → "6,3", "2.675" → "2,68"), como o Excel mostra a
 * célula. Sem casas (o `inteiro`) o meio vai pro par, igual ao round() do
 * Python (2,5 → 2). O % s/ vendas vem do servidor com 2 casas e sai com 1:
 * "x,x5" é comum — os empates estão em tests/conferencia-arredondamento.json,
 * que os testes dos dois lados conferem.
 */
function digitosMeioParaCima(v: number, casas: number): { inteiro: string; fracao: string } {
  // "6.35", "1e-7", "1.5e+21": mantissa + expoente da forma curta.
  const [mantissa, expoente = '0'] = String(Math.abs(v)).split('e')
  const [i, f = ''] = mantissa.split('.')
  let todos = i + f
  let ponto = i.length + Number(expoente)
  if (ponto < 0) {
    todos = '0'.repeat(-ponto) + todos
    ponto = 0
  }
  if (ponto > todos.length) todos += '0'.repeat(ponto - todos.length)
  const fracaoToda = todos.slice(ponto)
  const resto = fracaoToda.slice(casas)
  let digitos = (todos.slice(0, ponto) || '0') + fracaoToda.slice(0, casas).padEnd(casas, '0')
  const ultimoImpar = Number(digitos[digitos.length - 1]) % 2 === 1
  const sobe = casas > 0
    ? resto[0] >= '5'
    : resto[0] > '5' || (resto[0] === '5' && (/[1-9]/.test(resto.slice(1)) || ultimoImpar))
  if (sobe) {
    // +1 no último dígito, com "vai um".
    const d = digitos.split('')
    let k = d.length - 1
    while (k >= 0 && d[k] === '9') d[k--] = '0'
    if (k >= 0) d[k] = String(Number(d[k]) + 1)
    else d.unshift('1')
    digitos = d.join('')
  }
  const corte = digitos.length - casas
  return { inteiro: digitos.slice(0, corte) || '0', fracao: digitos.slice(corte) }
}
function milhar(inteiro: string): string {
  return inteiro.replace(/\B(?=(\d{3})+(?!\d))/g, '.')
}
/** 1234.5 → "1.234,5" (pt-BR), arredondado como o servidor. */
function decimais(v: number, casas: number): string {
  const { inteiro, fracao } = digitosMeioParaCima(v, casas)
  // O Python escreve o sinal até quando arredonda pra zero (-0,04 → "-0,0").
  const sinal = v < 0 || Object.is(v, -0) ? '-' : ''
  return `${sinal}${milhar(inteiro)}${casas > 0 ? `,${fracao}` : ''}`
}

/** "R$ 1.234,56" (espaço comum depois do R$, igual ao servidor); null → "—". */
export function dinheiro(v: number | null | undefined): string {
  if (!ok(v)) return '—'
  return `${v < 0 ? '-' : ''}R$ ${decimais(Math.abs(v), 2)}`
}
/** "72.388"; null → "—". Arredonda pro inteiro como o round() do Python (2,5 → 2). */
export function inteiro(v: number | null | undefined): string {
  if (!ok(v)) return '—'
  const { inteiro: i } = digitosMeioParaCima(v, 0)
  return `${v < 0 && i !== '0' ? '-' : ''}${milhar(i)}`
}
/**
 * 8.2 → "8,2%" (o valor já vem multiplicado por 100); null → "—". Com
 * `casas` = 2 é o da planilha do Resumo: 7.5 → "7,50%" (empates em
 * tests/conferencia-arredondamento.json, "percentual_2casas").
 */
export function percentual(v: number | null | undefined, casas = 1): string {
  if (!ok(v)) return '—'
  return `${decimais(v, casas)}%`
}
export function fmtValor(v: number | null | undefined, tipo: TipoMetrica): string {
  if (tipo === 'dinheiro') return dinheiro(v)
  if (tipo === 'percentual') return percentual(v)
  return inteiro(v)
}
/**
 * Casas do % na planilha do Resumo — no valor ("7,50%") e na variação em p.p.
 * ("▼ 0,01 p.p."). O CASAS_PLANILHA do servidor (calculo.py) é o mesmo.
 */
export const CASAS_PLANILHA = 2
/** Célula da planilha do Resumo: igual ao fmtValor, mas % com 2 casas ("7,50%"). */
export function fmtPlanilha(v: number | null | undefined, tipo: TipoMetrica): string {
  return tipo === 'percentual' ? percentual(v, CASAS_PLANILHA) : fmtValor(v, tipo)
}
/** Valor de uma métrica numa semana; ausente ou não-número = null. */
export function valor(s: Valores | null | undefined, chave: ChaveMetrica): number | null {
  const v = s?.[chave]
  return ok(v) ? v : null
}

/** Soma de grupo: null + x = x; tudo null = null. */
export function soma(vs: (number | null | undefined)[]): number | null {
  let total: number | null = null
  for (const v of vs) if (ok(v)) total = (total ?? 0) + v
  return total
}
/** Investimento = afiliados + Ads (null + x = x; os dois null = null). */
export function investimento(s: Valores | null | undefined): number | null {
  return soma([valor(s, 'invest_afiliados'), valor(s, 'invest_ads')])
}
/** % s/ vendas = investimento ÷ vendas × 100; null sem vendas (ou 0) ou sem investimento. */
export function pctDe(invest: number | null, vendas: number | null): number | null {
  if (!ok(invest) || !ok(vendas) || vendas === 0) return null
  return (invest / vendas) * 100
}
/** Conversão = pedidos ÷ cliques × 100; null sem cliques (0 ou negativo) ou sem pedidos — nunca negativa. */
export function conversaoDe(pedidos: number | null, cliques: number | null): number | null {
  if (!ok(pedidos) || !ok(cliques) || cliques <= 0 || pedidos < 0) return null
  return (pedidos / cliques) * 100
}
// Conversão → de onde ela sai (a média das 3 semanas usa as somas, não os %).
const BASE_CONVERSAO: Partial<Record<ChaveMetrica, [ChaveMetrica, ChaveMetrica]>> = {
  conversao_afiliados: ['pedidos_afiliados', 'cliques_afiliados'],
  conversao_ads: ['pedidos_ads', 'cliques_ads'],
}

// ── variação ────────────────────────────────────────────────────────────

export type Cor = 'verde' | 'vermelho' | 'cinza'
export interface Variacao {
  texto: string
  direcao: 'sobe' | 'desce' | null
  cor: Cor
}
const SEM_VARIACAO: Variacao = { texto: '—', direcao: null, cor: 'cinza' }
const IGUAL: Variacao = { texto: '=', direcao: null, cor: 'cinza' }

function corDe(direcao: 'sobe' | 'desce', bom: Bom): Cor {
  if (bom === 'neutro') return 'cinza'
  return direcao === bom ? 'verde' : 'vermelho'
}

/**
 * Variação de `anterior` para `atual` (regras do contrato §5):
 * - dinheiro/inteiro: (atual − anterior) ÷ |anterior| × 100 → "▲ 12,3%" / "▼ 4,1%";
 *   anterior 0 e atual > 0 → "novo"; os dois 0 (ou iguais) → "="; algum null → "—".
 * - percentual (% s/ vendas, conversão): diferença em pontos → "▲ 1,2 p.p." / "▼ 0,8 p.p.";
 *   iguais → "=". `casas` = casas dos p.p.: a planilha do Resumo usa CASAS_PLANILHA (2), como o
 *   % dela ("▼ 0,01 p.p."; com 1 casa, 0,44% → 0,43% sairia "▼ 0,0 p.p." em vermelho).
 * - cor: bom 'sobe' → subir verde, cair vermelho; 'desce' ao contrário; 'neutro' sempre cinza.
 * - Saldo Ads nunca é "novo" (sai "—"): saldo zerado na semana passada não é "conta nova".
 */
export function variacao(
  atual: number | null | undefined,
  anterior: number | null | undefined,
  tipo: TipoMetrica,
  bom: Bom,
  chave?: ChaveMetrica,
  casas = 1,
): Variacao {
  if (!ok(atual) || !ok(anterior)) return SEM_VARIACAO
  if (tipo === 'percentual') {
    const d = atual - anterior
    if (d === 0) return IGUAL
    const direcao = d > 0 ? 'sobe' : 'desce'
    return { texto: `${d > 0 ? '▲' : '▼'} ${decimais(Math.abs(d), casas)} p.p.`, direcao, cor: corDe(direcao, bom) }
  }
  if (anterior === 0) {
    if (atual === 0) return IGUAL
    // Saldo nunca é "novo"; e de 0 para negativo não há porcentagem que diga algo.
    if (chave === 'saldo_ads' || atual < 0) return SEM_VARIACAO
    return { texto: 'novo', direcao: 'sobe', cor: corDe('sobe', bom) }
  }
  const p = ((atual - anterior) / Math.abs(anterior)) * 100
  if (p === 0) return IGUAL
  const direcao = p > 0 ? 'sobe' : 'desce'
  return { texto: `${p > 0 ? '▲' : '▼'} ${decimais(Math.abs(p), 1)}%`, direcao, cor: corDe(direcao, bom) }
}

/**
 * Média das 3 semanas anteriores (S2..S4) pra "vs média 3 sem.": só os valores
 * que existem entram na média; nenhum → null. Pro % s/ vendas não é a média dos
 * percentuais: é Σinvestimento ÷ Σvendas das três semanas. Pra conversão, do
 * mesmo jeito: Σpedidos ÷ Σcliques.
 */
export function media3(semanas: Valores[] | null | undefined, chave: ChaveMetrica): number | null {
  const anteriores = (semanas ?? []).slice(1, 4)
  if (chave === 'pct') {
    return pctDe(soma(anteriores.map((s) => investimento(s))), soma(anteriores.map((s) => valor(s, 'vendas'))))
  }
  const base = BASE_CONVERSAO[chave]
  if (base) {
    return conversaoDe(soma(anteriores.map((s) => valor(s, base[0]))), soma(anteriores.map((s) => valor(s, base[1]))))
  }
  const vs = anteriores.map((s) => valor(s, chave)).filter(ok)
  return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null
}

/** Uma célula da tabela do grupo: valor da semana, o da anterior e a variação. */
export function celula(semanas: Valores[] | null | undefined, m: Metrica) {
  const atual = valor(semanas?.[0], m.chave)
  const anterior = valor(semanas?.[1], m.chave)
  return {
    valor: fmtValor(atual, m.tipo),
    anterior: fmtValor(anterior, m.tipo),
    variacao: variacao(atual, anterior, m.tipo, m.bom, m.chave),
    // Sem número nas duas semanas, a linha "ant." não diz nada.
    vazia: atual === null && anterior === null,
  }
}

/** Linha de "Últimas 4 semanas": S1..S4 + vs semana anterior + vs média 3 sem. */
export function linhaQuatroSemanas(semanas: Valores[] | null | undefined, m: Metrica) {
  const s = semanas ?? []
  const atual = valor(s[0], m.chave)
  return {
    valores: [0, 1, 2, 3].map((i) => fmtValor(valor(s[i], m.chave), m.tipo)),
    vsAnterior: variacao(atual, valor(s[1], m.chave), m.tipo, m.bom, m.chave),
    vsMedia: variacao(atual, media3(s, m.chave), m.tipo, m.bom, m.chave),
  }
}

/** Cartão de resumo de um grupo: Vendas, Investimento e % s/ vendas, contra a semana anterior. */
export function resumoCartao(total: TotalGrupo | null | undefined) {
  const s0 = total?.semanas?.[0]
  const s1 = total?.semanas?.[1]
  const linha = (atual: number | null, anterior: number | null, tipo: TipoMetrica, bom: Bom) => ({
    valor: fmtValor(atual, tipo),
    anterior: fmtValor(anterior, tipo),
    variacao: variacao(atual, anterior, tipo, bom),
  })
  return {
    vendas: linha(valor(s0, 'vendas'), valor(s1, 'vendas'), 'dinheiro', 'sobe'),
    investimento: linha(investimento(s0), investimento(s1), 'dinheiro', 'neutro'),
    pct: linha(valor(s0, 'pct'), valor(s1, 'pct'), 'percentual', 'desce'),
  }
}

// ── datas ───────────────────────────────────────────────────────────────

/** "2026-09-28" → "28/09". */
export function ddmm(d: string | null | undefined): string {
  if (!d || d.length < 10) return d || ''
  return `${d.slice(8, 10)}/${d.slice(5, 7)}`
}
/** "2026-10-04" → "04/10/2026". */
export function ddmmaaaa(d: string | null | undefined): string {
  if (!d || d.length < 10) return d || ''
  return `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`
}
/** Rótulo da semana: o do relatório, ou "28/09–04/10" montado das datas. */
export function rotuloSemana(s: SemanaPeriodo | null | undefined): string {
  if (!s) return '—'
  return s.rotulo || `${ddmm(s.inicio)}–${ddmm(s.fim)}`
}
/** "Conferência Shopee — 28/09 a 04/10/2026". */
export function tituloRelatorio(semanas: SemanaPeriodo[] | null | undefined): string {
  const s1 = semanas?.[0]
  if (!s1) return 'Conferência Shopee'
  return `Conferência Shopee — ${ddmm(s1.inicio)} a ${ddmmaaaa(s1.fim)}`
}
/** "comparado com 21/09 a 27/09". */
export function comparadoCom(semanas: SemanaPeriodo[] | null | undefined): string {
  const s2 = semanas?.[1]
  return s2 ? `comparado com ${ddmm(s2.inicio)} a ${ddmm(s2.fim)}` : ''
}
/** ISO → "06/10/2026 às 16:41" no horário de Brasília. */
export function dataHoraBr(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const tz = { timeZone: 'America/Sao_Paulo' } as const
  const dia = d.toLocaleDateString('pt-BR', { ...tz, day: '2-digit', month: '2-digit', year: 'numeric' })
  const hora = d.toLocaleTimeString('pt-BR', { ...tz, hour: '2-digit', minute: '2-digit' })
  return `${dia} às ${hora}`
}
/** ISO → "16:41" no horário de Brasília. */
export function horaBr(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' })
}

/** Rótulo da execução no seletor: "06/10 às 13:30 · semanal · 28/09–04/10 · pronto". */
export function rotuloExecucao(e: ExecucaoResumo): string {
  const quando = dataHoraBr(e.criado_em).replace(/\/\d{4} às/, ' às')
  return [quando, e.tipo, rotuloSemana(e.semanas?.[0]), rotuloStatusExecucao(e.status)].filter(Boolean).join(' · ')
}

/** Nome do arquivo igual ao do servidor: conferencia-shopee-<S1.inicio>_<S1.fim>.<ext>. */
export function nomeArquivo(semanas: SemanaPeriodo[] | null | undefined, fmt: Formato): string {
  const s1 = semanas?.[0]
  return s1 ? `conferencia-shopee-${s1.inicio}_${s1.fim}.${fmt}` : `conferencia-shopee.${fmt}`
}
/** Nome do Content-Disposition (filename*=UTF-8''… ou filename="…"); sem nome → null. */
export function nomeDoCabecalho(cd: string | null | undefined): string | null {
  if (!cd) return null
  const estrela = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(cd)
  if (estrela) {
    try {
      return decodeURIComponent(estrela[1].trim().replace(/^"|"$/g, ''))
    } catch {
      // nome mal codificado: tenta o filename comum
    }
  }
  const m = /filename\s*=\s*"?([^";]+)"?/.exec(cd)
  return m ? m[1].trim() : null
}

/** "1 conta" / "4 contas". */
export function contasTxt(n: number | null | undefined): string {
  const v = n ?? 0
  return `${v} ${v === 1 ? 'conta' : 'contas'}`
}

// ── planilha do Resumo (07/10/2026) ─────────────────────────────────────
//
// O desenho da planilha antiga do dono ("mais ou menos desse jeito"):
//
//                   |   07/09 a 13/09   | … |   28/09 a 04/10   | Variação (28/09–04/10 × 21/09–27/09)
//   Métrica         | Mala|Cel.|Eletro|Geral | … | Mala|Cel.|Eletro|Geral | Mala|Cel.|Eletro|Geral
//   Vendas    afiliados | …
//             Ads       | …
//
// Semanas da mais VELHA pra mais nova (S4 → S1), da esquerda pra direita, e no
// fim a variação S1 × S2 de cada grupo, com as regras de sempre (variacao()) —
// em p.p. com 2 casas, como o % da planilha.
// O Saldo Ads não entra aqui (continua no relatório e no CSV). O Excel e o HTML
// do servidor desenham a mesma coisa.

export type ChaveColunaPlanilha = ChaveGrupo | 'geral'

/** As 4 colunas de cada semana (e da Variação), nesta ordem. */
export const GRUPOS_PLANILHA: { chave: ChaveColunaPlanilha; rotulo: string }[] = [
  { chave: 'mala', rotulo: 'Mala' },
  { chave: 'celular', rotulo: 'Celular' },
  { chave: 'eletro', rotulo: 'Eletro' },
  { chave: 'geral', rotulo: 'Geral' },
]

/**
 * As linhas, na ordem da planilha: categoria (mesclada nas linhas seguidas
 * dela) + sub-rótulo. `chave` null = não existe na Shopee (impressões de
 * afiliados): sempre "—".
 */
export const LINHAS_PLANILHA: { categoria: string; sub: string; chave: ChaveMetrica | null }[] = [
  { categoria: 'Vendas', sub: 'afiliados', chave: 'vendas_afiliados' },
  { categoria: 'Vendas', sub: 'Ads', chave: 'vendas_ads' },
  { categoria: 'Impressões', sub: 'afiliados', chave: null },
  { categoria: 'Impressões', sub: 'Ads', chave: 'impressoes' },
  { categoria: 'Conversão', sub: 'afiliados', chave: 'conversao_afiliados' },
  { categoria: 'Conversão', sub: 'Ads', chave: 'conversao_ads' },
  { categoria: 'Investimento', sub: 'afiliados', chave: 'invest_afiliados' },
  { categoria: 'Investimento', sub: 'Ads', chave: 'invest_ads' },
  { categoria: 'Resumo', sub: '% investimento / vendas', chave: 'pct' },
  { categoria: 'Resumo', sub: 'Vendas no período', chave: 'vendas' },
]

/** Cabeçalho da semana na planilha: "07/09 a 13/09". */
export function semanaPlanilha(s: SemanaPeriodo | null | undefined): string {
  if (!s) return '—'
  return `${ddmm(s.inicio)} a ${ddmm(s.fim)}`
}
/** Cabeçalho do bloco final: "Variação (28/09–04/10 × 21/09–27/09)". */
export function rotuloVariacaoPlanilha(semanas: SemanaPeriodo[] | null | undefined): string {
  const [s1, s2] = semanas ?? []
  return s1 && s2 ? `Variação (${rotuloSemana(s1)} × ${rotuloSemana(s2)})` : 'Variação'
}

export interface LinhaPlanilha {
  chave: string // única na tabela (a métrica, ou "categoria|sub" pra linha sem métrica)
  categoria: string
  sub: string
  /** Linhas que a célula da categoria cobre (rowspan); 0 = coberta pela de cima, não desenha. */
  span: number
  /** [semana, da mais velha pra mais nova][grupo, na ordem de GRUPOS_PLANILHA] → texto. */
  valores: string[][]
  /** Variação S1 × S2 por grupo (mesma ordem de GRUPOS_PLANILHA). */
  variacoes: Variacao[]
}
export interface Planilha {
  semanas: { indice: number; rotulo: string }[] // indice no relatório (0 = S1); da mais velha pra mais nova
  grupos: { chave: ChaveColunaPlanilha; rotulo: string }[]
  variacao: string
  linhas: LinhaPlanilha[]
}

/**
 * A planilha do Resumo a partir do relatório congelado: total de cada grupo
 * (rel.grupos[].total) e o Geral (rel.geral). Grupo que falta, semana que
 * falta ou métrica que o relatório não tem (relatório de antes de 07/10 não
 * tem cliques/pedidos/conversão) viram "—"; nunca 0, nunca erro.
 */
export function planilhaResumo(
  rel: Pick<Relatorio, 'semanas' | 'grupos' | 'geral'> | null | undefined,
): Planilha | null {
  if (!rel) return null
  const periodos = (Array.isArray(rel.semanas) ? rel.semanas : []).slice(0, 4)
  const semanas = periodos.map((s, indice) => ({ indice, rotulo: semanaPlanilha(s) })).reverse()
  const grupos = Array.isArray(rel.grupos) ? rel.grupos : []
  const totais = GRUPOS_PLANILHA.map((g) =>
    g.chave === 'geral' ? rel.geral : grupos.find((x) => x?.chave === g.chave)?.total,
  )
  const semanaDe = (t: TotalGrupo | null | undefined, i: number) => (Array.isArray(t?.semanas) ? t.semanas[i] : undefined)
  const linhas = LINHAS_PLANILHA.map((def, k): LinhaPlanilha => {
    const m = def.chave ? METRICA[def.chave] : null
    let span = 0
    if (k === 0 || LINHAS_PLANILHA[k - 1].categoria !== def.categoria) {
      span = 1
      while (LINHAS_PLANILHA[k + span]?.categoria === def.categoria) span++
    }
    return {
      chave: m ? m.chave : `${def.categoria}|${def.sub}`,
      categoria: def.categoria,
      sub: def.sub,
      span,
      valores: semanas.map((s) => totais.map((t) => (m ? fmtPlanilha(valor(semanaDe(t, s.indice), m.chave), m.tipo) : '—'))),
      variacoes: totais.map((t) => (m
        ? variacao(valor(semanaDe(t, 0), m.chave), valor(semanaDe(t, 1), m.chave), m.tipo, m.bom, m.chave, CASAS_PLANILHA)
        : { ...SEM_VARIACAO })),
    }
  })
  return {
    semanas,
    grupos: GRUPOS_PLANILHA.map((g) => ({ ...g })),
    variacao: rotuloVariacaoPlanilha(periodos),
    linhas,
  }
}

// ---------- fim helpers puros
