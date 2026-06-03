// Helpers de data no fuso BRT (America/Sao_Paulo).
//
// Por que existem: `new Date().toISOString()` retorna UTC. Operador
// trabalha por dia local. Às 22h BRT (= 01h UTC do dia seguinte),
// `toISOString().slice(0, 10)` retorna o dia seguinte e a tela mostra
// "amanhã" como "hoje". Esse drift de 3h afeta filtros, defaults de
// data em modais, nomes de arquivo de exportação, etc.
//
// Usar `isoToday()` / `isoDateBrt(d)` no lugar de `.toISOString().slice(0,10)`
// resolve em todos esses lugares.

const _BRT_FMT = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Sao_Paulo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/**
 * Data BRT de um datetime (default: agora) no formato YYYY-MM-DD.
 * `en-CA` produz exatamente esse formato — não precisa parsear.
 */
export function isoDateBrt(d: Date = new Date()): string {
  return _BRT_FMT.format(d)
}

/** Data de hoje em BRT (YYYY-MM-DD). */
export function isoToday(): string {
  return isoDateBrt(new Date())
}

/** Data de N dias atrás em BRT (YYYY-MM-DD). N negativo = futuro. */
export function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return isoDateBrt(d)
}
