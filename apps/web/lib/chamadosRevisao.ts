// Coluna Revisão da aba Chamados (Vinicius 05/11/2026): rótulos, cores e ordem
// compartilhados pela tabela, pelo seletor e pelo Raio-X. O que a linha mostra hoje
// vem pronto da API em `revisao_estado` (services/chamados_revisao.py).

export type RevisaoTipo = 'conferido' | 'revisar' | 'fixo'
export type RevisaoEstado = 'falta' | RevisaoTipo | 'prazo_hoje' | 'prazo_vencido' | 'respondeu'

export type RevisaoCampos = {
  revisao_tipo: RevisaoTipo | null
  revisao_dia: string | null
  revisao_ate: string | null
  revisao_motivo: string | null
  revisao_por_nome: string | null
  revisao_estado: RevisaoEstado
}

// Painel por mês: GET /api/chamados/dias. Sem `tem_registro` não há revisão daquele dia.
export type RevisaoDia = {
  dia: string
  abertos: number
  novos: number
  concluidos: number
  lucro: number
  prejuizo: number
  acumulado: number
  tem_registro: boolean
  conferido: number
  revisar: number
  fixo: number
  falta: number
  prazos: number
  por_status: Record<string, number>
  faltaram: string[]
  fechado_at: string | null
  fechado_por_nome: string | null
}
export type RevisaoDias = { dias: RevisaoDia[]; pode_fechar: boolean }

// `tipo` nulo tira a marca; `ate` e `motivo` só no Fixo.
export type RevisaoMarca = { tipo: RevisaoTipo | null; ate?: string; motivo?: string }

export const REVISAO_DOT: Record<RevisaoEstado, string> = {
  falta: 'bg-gray-400',
  conferido: 'bg-emerald-600',
  revisar: 'bg-amber-600',
  fixo: 'bg-blue-600',
  prazo_hoje: 'bg-red-600',
  prazo_vencido: 'bg-red-600',
  respondeu: 'bg-red-600',
}

// O que precisa de gente vem primeiro; o fixado, por último.
export const REVISAO_ORDEM: Record<RevisaoEstado, number> = {
  prazo_hoje: 0,
  respondeu: 0,
  prazo_vencido: 0,
  falta: 1,
  revisar: 2,
  conferido: 3,
  fixo: 4,
}

export const REVISAO_PRECISA_VER: RevisaoEstado[] = ['prazo_hoje', 'respondeu', 'prazo_vencido']

// Filtro da coluna: `prazos` junta os três estados que pedem atenção.
export const REVISAO_FILTROS: { value: string; label: string }[] = [
  { value: 'prazos', label: 'Prazos' },
  { value: 'falta', label: 'Falta revisar' },
  { value: 'revisar', label: 'Revisar' },
  { value: 'conferido', label: 'Conferido' },
  { value: 'fixo', label: 'Fixo com data' },
]

export function passaNoFiltroRevisao(estado: RevisaoEstado, filtro: string): boolean {
  if (filtro === 'all') return true
  if (filtro === 'prazos') return REVISAO_PRECISA_VER.includes(estado)
  return estado === filtro
}

// '2026-11-25' → '25/11'
export function diaMes(iso: string | null | undefined): string {
  if (!iso) return ''
  const [, mes, dia] = iso.slice(0, 10).split('-')
  return dia && mes ? `${dia}/${mes}` : ''
}

export function somarDias(iso: string, dias: number): string {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + dias)
  return d.toISOString().slice(0, 10)
}

// Texto da marca: `texto` é o que a linha diz; `detalhe`, quando e quem.
export function rotuloRevisao(r: RevisaoCampos): { texto: string; detalhe: string } {
  const quem = [diaMes(r.revisao_dia), r.revisao_por_nome].filter(Boolean).join(' · ')
  switch (r.revisao_estado) {
    case 'conferido':
      return { texto: 'Conferido', detalhe: quem }
    case 'revisar':
      return { texto: 'Revisar', detalhe: quem }
    case 'fixo':
      return { texto: `Fixo até ${diaMes(r.revisao_ate)}`, detalhe: '' }
    case 'prazo_hoje':
      return { texto: 'Prazo chegou hoje', detalhe: '' }
    case 'prazo_vencido':
      return { texto: `Prazo venceu ${diaMes(r.revisao_ate)}`, detalhe: '' }
    case 'respondeu':
      return { texto: 'Plataforma respondeu', detalhe: '' }
    default:
      return { texto: 'Selecionar', detalhe: '' }
  }
}
