// Conciliação — rótulos, cores e formatação compartilhados pela página e pelos
// componentes (aba Pedidos, Extrato do dia, Resumo). Desenho em
// docs/conciliacao-avaliacao.md.

export const SITUACOES: Array<{ key: string; label: string; cls: string }> = [
  { key: 'bateu', label: 'Bateu', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' },
  { key: 'aguardando', label: 'Aguardando liberação', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'vencido', label: 'Não liberado (vencido)', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'recebido_a_menos', label: 'Recebido a menos', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'recebido_a_mais', label: 'Recebido a mais', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'estornado_depois', label: 'Estornado depois', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'cancelado', label: 'Cancelado', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200' },
  { key: 'sem_venda', label: 'Venda não lida', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200' },
]

export const ETAPAS: Array<{ key: string; label: string; cls: string }> = [
  { key: 'aguardando', label: 'aguardando', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'conciliado', label: 'conciliado', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' },
  { key: 'pendencia', label: 'pendência', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'finalizado', label: 'finalizado', cls: 'bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100' },
  { key: 'reaberto', label: 'reaberto', cls: 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300' },
]

// Status de uma linha do extrato na conciliação (Extrato do dia).
export const STATUS_MOV: Record<string, { label: string; cls: string; dot: string }> = {
  automatico: { label: 'casou sozinho', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300', dot: '●' },
  manual: { label: 'vinculado à mão', cls: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300', dot: '●' },
  classificado: { label: 'sem pedido, classificado', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200', dot: '●' },
  anterior: { label: 'pedido anterior ao início', cls: 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300', dot: '○' },
  pendente: { label: 'pendente', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300', dot: '●' },
  divergente: { label: 'divergente', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300', dot: '●' },
}

export const TIPOS_MOV: Record<string, string> = {
  venda_liberada: 'Venda liberada',
  mediacao: 'Mediação (reclamação perdida)',
  mediacao_cancelada: 'Mediação cancelada',
  estorno: 'Estorno',
  chargeback: 'Chargeback',
  disputa: 'Disputa',
  reserva_disputa: 'Reserva por reclamação',
  reserva_estorno: 'Reserva pra estorno',
  reserva_frete_devolucao: 'Reserva do frete de devolução',
  reserva_saque: 'Reserva pro saque',
  reserva_pagamento: 'Reserva de pagamento',
  saque: 'Saque',
  frete: 'Frete',
  frete_cancelado: 'Frete cancelado',
  frete_devolucao: 'Frete de devolução',
  antecipacao: 'Antecipação',
  saque_confirmado: 'Saque confirmado',
  saque_cancelado: 'Saque cancelado',
  ajuste_shopee: 'Ajuste da Shopee',
  ajuste_tiktok: 'Ajuste da TikTok',
  publicidade: 'Publicidade (Ads)',
  credito: 'Crédito',
  imposto: 'Imposto',
  outro: 'Outro',
  fatura_tarifa_venda: 'Fatura: custo por vender',
  fatura_tarifa_cobranca: 'Fatura: custo por cobrar',
  fatura_parcelamento: 'Fatura: taxa de parcelamento',
  fatura_parcelamento_comprador: 'Fatura: parcelamento pago pelo comprador',
  fatura_frete: 'Fatura: Mercado Envios',
  fatura_publicidade: 'Fatura: publicidade',
  fatura_bonificacao: 'Fatura: bonificação (devolução de tarifa)',
  fatura_outro: 'Fatura: outra cobrança',
}

export function fmtMoney(v: number | null | undefined, vazio = '—') {
  if (v === null || v === undefined) return vazio
  if (Math.abs(v) < 0.005) v = 0 // nada de "-0,00"
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
export function fmtPct(v: number | null | undefined) {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
}
export function fmtDia(v: string | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
}
export function fmtDiaHora(v: string | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
export function fmtHora(v: string | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}
export function sitCls(key: string) {
  return SITUACOES.find((s) => s.key === key)?.cls || 'bg-slate-200 text-slate-700'
}
export function etapaCls(key: string) {
  return ETAPAS.find((s) => s.key === key)?.cls || 'bg-slate-200 text-slate-700'
}
export function tipoMov(t: string) {
  return TIPOS_MOV[t] || t
}
export function sinal(v: number | null | undefined, cls: { pos: string; neg: string }) {
  if (v === null || v === undefined) return ''
  if (v > 0.005) return cls.pos
  if (v < -0.005) return cls.neg
  return ''
}
// Datas no fuso do navegador (toISOString() é UTC: às 21h de Brasília já seria "amanhã").
function isoLocal(d: Date) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
export function ontemISO() {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return isoLocal(d)
}
export function somaDias(iso: string, n: number) {
  const d = new Date(iso + 'T12:00:00')
  d.setDate(d.getDate() + n)
  return isoLocal(d)
}
