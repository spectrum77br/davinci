// Cadastros › Contabilidade › Compra (23/10/2026). Eduardo: "do lado de emissao de
// servico vai ter a nova aba compra": a empresa do grupo compra embalagem (ou
// outro produto do catálogo) de uma das contas Bling de NF, com base no
// faturamento do mês; a compra vira a planilha de pedidos do Bling da vendedora.
// Tipos e contas iguais ao servidor (apps/api/app/services/compra.py).

export type Endereco = {
  logradouro: string | null
  numero: string | null
  complemento: string | null
  bairro: string | null
  cep: string | null
  municipio: string | null
  uf: string | null
}

export type Comprador = {
  company_id: string
  razao_social: string
  apelido: string | null
  cnpj: string
  inscricao_estadual: string | null
  endereco: Endereco
  endereco_origem: 'nfeio' | 'nota' | null
}

export type Vendedora = {
  cnpj: string
  nome: string
  uf: string | null
  municipio: string | null
  notas_90d: number
  ultima: string | null
}

export type Produto = {
  id: string
  nome: string
  sku: string
  ncm: string
  cfop: string | null
  unidade: string
  preco_padrao: string | null
  ativo: boolean
  usado: number
}

export type FaturamentoCompra = {
  company_id: string
  competencia: string
  valor: string
  pedidos: number
  lojas: { plataforma: string; conta: string; pedidos: number; valor: string }[]
  tem_loja: boolean
}

export type ItemCompra = {
  id: string
  produto_id: string | null
  nome: string
  sku: string
  ncm: string
  unidade: string
  quantidade: number
  valor_unitario: string
  valor_total: string
}

export type StatusCompra = 'registrada' | 'nf_emitida' | 'cancelada'

export type Compra = {
  id: string
  numero: number
  company_id: string
  comprador: {
    razao_social: string
    apelido: string | null
    cnpj: string
    inscricao_estadual: string | null
    endereco: Endereco
    endereco_origem: 'nfeio' | 'nota' | 'digitado' | null
  }
  vendedor_cnpj: string
  vendedor_nome: string
  vendedor_uf: string | null
  competencia: string
  data_pedido: string
  faturamento: string | null
  modo: 'quantidade' | 'percentual'
  percentual: string | null
  cfop: string
  total: string
  status: StatusCompra
  nf_numero: string | null
  observacoes: string | null
  itens: ItemCompra[]
  created_at: string
  updated_at: string
  criado_por: string | null
}

export const ROTULO_STATUS: Record<StatusCompra, { texto: string; classe: string }> = {
  registrada: { texto: 'Registrada', classe: 'pill-warning' },
  nf_emitida: { texto: 'NF emitida', classe: 'pill-success' },
  cancelada: { texto: 'Cancelada', classe: 'pill-muted' },
}

export const ORIGEM_ENDERECO: Record<string, string> = {
  nfeio: 'endereço do cadastro da NFE.io',
  nota: 'endereço da última NF que a própria empresa emitiu',
  digitado: 'endereço digitado na compra',
}

// Igual ao servidor: 5102 dentro do estado, 6102 para outro (UF desconhecida = 5102).
export function cfopSugerido(ufVendedora: string | null | undefined, ufCompradora: string | null | undefined): string {
  if (ufVendedora && ufCompradora && ufVendedora.toUpperCase() !== ufCompradora.toUpperCase()) return '6102'
  return '5102'
}

// Quantas unidades cabem em pct% do faturamento ao preço (arredonda para baixo).
// Em centavos para não errar por ponto flutuante: 41.850 × 10% ÷ 9,30 = 450.
export function quantidadePeloPercentual(faturamento: string | number, pct: string | number, preco: string | number): number {
  const fat = Math.round(Number(faturamento) * 100)
  const p = Number(pct)
  const pr = Math.round(Number(preco) * 100)
  if (!Number.isFinite(fat) || !Number.isFinite(p) || !Number.isFinite(pr) || fat <= 0 || p <= 0 || pr <= 0) return 0
  // fat (centavos) × p / 100 = alvo em centavos; ÷ preço em centavos
  return Math.floor((fat * p) / 100 / pr + 1e-9)
}

// total da linha em centavos (meia-unidade arredonda para cima, como o servidor)
export function totalDaLinha(quantidade: number, unitario: string | number): number {
  const q = Number(quantidade)
  const u = Number(unitario)
  if (!Number.isFinite(q) || !Number.isFinite(u)) return 0
  return Math.round(q * u * 100 + 1e-7) / 100
}

export function fmtCep(c: string | null | undefined): string {
  const d = (c ?? '').replace(/\D/g, '')
  return d.length === 8 ? `${d.slice(0, 5)}-${d.slice(5)}` : d
}

export function fmtNcm(n: string | null | undefined): string {
  const d = (n ?? '').replace(/\D/g, '')
  return d.length === 8 ? `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}` : d
}

export function fmtQtd(n: number | string): string {
  const v = Number(n)
  return Number.isFinite(v) ? v.toLocaleString('pt-BR') : String(n)
}

// "1.000" (milhar) ou "450" → 450; vazio ou torto → null. Igual ao servidor:
// "1.5" e "4.50" NÃO viram 15 e 450 (o ponto só vale como milhar).
export function lerQuantidade(t: string | number | null | undefined): number | null {
  if (typeof t === 'number') return Number.isInteger(t) && t > 0 ? t : null
  let s = (t ?? '').trim()
  if (/^[1-9]\d{0,2}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '')
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n > 0 ? n : null
}

export function enderecoEmUmaLinha(e: Partial<Endereco> | null | undefined): string {
  if (!e) return ''
  const rua = [e.logradouro, e.numero].filter(Boolean).join(', ')
  const partes = [rua, e.complemento, e.bairro, [e.municipio, e.uf].filter(Boolean).join('/'), fmtCep(e.cep)]
  return partes.filter((p) => p && String(p).trim()).join(' · ')
}

export const CAMPOS_ENDERECO: { campo: keyof Endereco; rotulo: string; obrigatorio: boolean }[] = [
  { campo: 'logradouro', rotulo: 'Rua', obrigatorio: true },
  { campo: 'numero', rotulo: 'Número', obrigatorio: true },
  { campo: 'complemento', rotulo: 'Complemento', obrigatorio: false },
  { campo: 'bairro', rotulo: 'Bairro', obrigatorio: true },
  { campo: 'cep', rotulo: 'CEP', obrigatorio: true },
  { campo: 'municipio', rotulo: 'Cidade', obrigatorio: true },
  { campo: 'uf', rotulo: 'UF', obrigatorio: true },
]

export function faltandoNoEndereco(e: Partial<Endereco>): string[] {
  return CAMPOS_ENDERECO.filter((c) => c.obrigatorio && !(e[c.campo] ?? '').toString().trim()).map((c) => c.rotulo)
}

export function nomeDaPlanilha(c: Pick<Compra, 'numero' | 'vendedor_nome'>[]): string {
  const vend = (c[0]?.vendedor_nome ?? 'vendedora')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
    .slice(0, 40) || 'vendedora'
  return c.length === 1 ? `compra_${c[0]!.numero}_${vend}.csv` : `compras_${c.length}_${vend}.csv`
}
