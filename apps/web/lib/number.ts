// Helpers numéricos com tolerância a formato BR (vírgula decimal).
//
// Por que existem: `parseFloat("5,08")` em JS retorna 5 — a vírgula é
// vista como fim do número e os centavos são perdidos silenciosamente.
// Operador digita sempre em PT-BR (vírgula). Usar `parseBRNumber()` no
// lugar de `Number(...)` resolve em todo input editável de número.

/**
 * Parse de número aceitando formatos BR e US.
 *
 * - `"5,08"` → 5.08 (vírgula = decimal BR)
 * - `"5.08"` → 5.08 (ponto = decimal US, também aceito)
 * - `"1.234,56"` → 1234.56 (ponto = milhar BR + vírgula = decimal)
 * - `null` / `""` → `null`
 */
export function parseBRNumber(input: string | number | null | undefined): number | null {
  if (input == null || input === '') return null
  if (typeof input === 'number') return Number.isFinite(input) ? input : null
  const s = String(input).trim()
  if (s === '') return null
  let normalized: string
  if (s.includes(',') && s.includes('.')) {
    // Formato BR com milhar: remove pontos, troca vírgula por ponto.
    normalized = s.replace(/\./g, '').replace(',', '.')
  } else if (s.includes(',')) {
    normalized = s.replace(',', '.')
  } else {
    normalized = s
  }
  const n = parseFloat(normalized)
  return Number.isFinite(n) ? n : null
}

/** Formata número em PT-BR. Máximo `decimals` casas, mínimo 0. */
export function formatBRNumber(n: number | null | undefined, decimals: number = 4): string {
  if (n == null || !Number.isFinite(n)) return ''
  return n.toLocaleString('pt-BR', { maximumFractionDigits: decimals })
}

/**
 * Formata decimal (`0.14`) como percentual sem o símbolo (`"14"`).
 * Símbolo `%` fica no template pra não interferir no parser do blur.
 */
export function formatPercent(decimal: number | null | undefined, decimals: number = 2): string {
  if (decimal == null || !Number.isFinite(decimal)) return ''
  return (decimal * 100).toLocaleString('pt-BR', { maximumFractionDigits: decimals })
}
