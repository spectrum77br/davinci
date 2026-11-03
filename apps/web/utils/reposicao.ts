// Reposição (02/11/2026, tarefas "Ajustes de colunas" e "Melhorias na Tabela
// de Preços" do Eduardo). Uma conta só, usada na Importação e na Tabela de
// Preços:
//
//   necessário = vendas dos últimos 30 dias ÷ 30 × (tempo de reposição + tempo de estoque)
//   Reposição  = estoque − necessário (a parte das vendas arredonda para cima)
//
// Negativo = falta estoque para cobrir o prazo: é quanto comprar (aparece em
// vermelho). Exemplo da tarefa: estoque 0, vende 100 por mês, 30 + 30 dias →
// necessário 200, Reposição −200. Positivo = sobra além do prazo.
// Os prazos são os da Importação, por departamento (import_config).

export interface ResultadoReposicao {
  /** estoque − necessário; negativo = falta. */
  valor: number
  /** Unidades para cobrir o prazo (vendas/dia × dias, arredondado para cima). */
  necessario: number
  /** tempo de reposição + tempo de estoque. */
  dias: number
}

/** Prazo em dias como número, ou null quando vazio/inválido (campo apagado
 *  no v-model.number vira '' — não é zero dia). */
export function prazoDias(v: unknown): number | null {
  if (v === '' || v == null) return null
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 ? n : null
}

/** null quando falta dado (sem estoque lido ou sem prazo válido). */
export function calcularReposicao(
  estoque: number | null | undefined,
  vendas30d: number | null | undefined,
  tempoReposicao: unknown,
  tempoEstoque: unknown,
): ResultadoReposicao | null {
  if (estoque == null || !Number.isFinite(Number(estoque))) return null
  const tr = prazoDias(tempoReposicao)
  const te = prazoDias(tempoEstoque)
  if (tr == null || te == null) return null
  const dias = tr + te
  const vendas = Math.max(0, Number(vendas30d) || 0)
  // Inteiro × inteiro ÷ 30: o ceil sobre a fração exata não sofre com
  // ponto flutuante (100 × 60 / 30 = 200, não 200,00000001).
  const necessario = Math.ceil((vendas * dias) / 30)
  return { valor: Math.trunc(Number(estoque)) - necessario, necessario, dias }
}

/** Texto do balão de uma célula de Reposição. */
export function explicarReposicao(
  r: ResultadoReposicao | null,
  estoque: number | null | undefined,
  vendas30d: number | null | undefined,
): string {
  if (!r) return 'Sem estoque lido ou sem prazos configurados.'
  const vendas = Math.max(0, Number(vendas30d) || 0)
  const vendido = vendas === 1 ? '1 venda' : `${vendas} vendas`
  const necessarias = r.necessario === 1 ? '1 necessária' : `${r.necessario} necessárias`
  const base = `${vendido} em 30 dias × ${r.dias} dias ÷ 30 = ${necessarias}; estoque ${Math.trunc(Number(estoque))}`
  if (r.valor < 0) return `${base} → faltam ${-r.valor} (comprar ${-r.valor}).`
  if (r.valor > 0) return `${base} → sobram ${r.valor}.`
  return `${base} → exato, nada a comprar.`
}

export interface ReposicaoDaPeca {
  peca: string
  estoque: number
  vendas30d: number
  r: ResultadoReposicao
}

export interface ReposicaoDaLinha {
  /** Soma das faltas (negativo) quando alguma peça falta; senão a soma das sobras. */
  valor: number
  pecas: ReposicaoDaPeca[]
  faltando: ReposicaoDaPeca[]
}

/** Reposição de uma linha com várias peças (cores/lotes), peça a peça: a
 *  sobra de uma cor não cobre a falta de outra. Com alguma peça faltando, a
 *  linha mostra a soma só das faltas (quanto comprar); sem nenhuma, a soma
 *  das sobras. null sem prazo válido. */
export function reposicaoPorPeca(
  estoquePorPeca: Record<string, number>,
  vendasPorPeca: Record<string, number>,
  tempoReposicao: unknown,
  tempoEstoque: unknown,
): ReposicaoDaLinha | null {
  if (prazoDias(tempoReposicao) == null || prazoDias(tempoEstoque) == null) return null
  const nomes = [...new Set([...Object.keys(estoquePorPeca), ...Object.keys(vendasPorPeca)])].sort()
  const pecas: ReposicaoDaPeca[] = []
  for (const peca of nomes) {
    const estoque = Number(estoquePorPeca[peca] ?? 0) || 0
    const vendas30d = Number(vendasPorPeca[peca] ?? 0) || 0
    const r = calcularReposicao(estoque, vendas30d, tempoReposicao, tempoEstoque)
    if (r) pecas.push({ peca, estoque, vendas30d, r })
  }
  const faltando = pecas.filter((p) => p.r.valor < 0)
  const somar = (lista: ReposicaoDaPeca[]) => lista.reduce((t, p) => t + p.r.valor, 0)
  return { valor: faltando.length ? somar(faltando) : somar(pecas), pecas, faltando }
}

/** Texto curto da conta, igual nas duas telas. */
export function textoFormulaReposicao(tempoReposicao: unknown, tempoEstoque: unknown): string {
  const tr = prazoDias(tempoReposicao)
  const te = prazoDias(tempoEstoque)
  const dias = `${tr ?? '?'} + ${te ?? '?'}`
  return `Reposição = Estoque − vendas 30d × (${dias}) ÷ 30; a parte das vendas arredonda para cima`
}
