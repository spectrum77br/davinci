// Cadastros › Imobilizado (06/10/2026): regras de tela do cadastro de bens.
// Valor sempre trafega como texto ("6890.00") para não perder centavo em float.

export type Pessoa = { id: string; nome: string; ativo?: boolean | null }

export type ImobilizadoItem = {
  id: number
  numero: string
  descricao: string
  valor: string
  responsavel: Pessoa
  status: 'ativo' | 'baixado'
  baixa_data: string | null
  baixa_motivo: string | null
  criado_em: string
  criado_por: Pessoa | null
  atualizado_em: string | null
  atualizado_por: Pessoa | null
}

export type ImobilizadoLista = { itens: ImobilizadoItem[]; quantidade: number; soma: string }

export type ImobilizadoHistorico = {
  id: number
  campo: string
  valor_anterior: string | null
  valor_novo: string | null
  alterado_em: string
  alterado_por: Pessoa | null
}

export const CAMPO_LABELS: Record<string, string> = {
  descricao: 'Descrição',
  valor: 'Valor',
  responsavel: 'Responsável',
  status: 'Status',
}

export const STATUS_LABELS: Record<string, string> = { ativo: 'Ativo', baixado: 'Baixado' }

export const IMOBILIZADO_ERROS: Record<string, string> = {
  numero_duplicado: 'Já existe um item com esse número.',
  responsavel_inativo: 'Escolha um usuário ativo do DaVinci.',
  item_nao_encontrado: 'Item não encontrado.',
  item_baixado: 'Item baixado não pode ser alterado.',
  baixa_data_futura: 'A data da baixa não pode ser depois de hoje.',
  mesma_pessoa: 'Escolha outra pessoa para receber os itens.',
  usuario_nao_encontrado: 'Usuário não encontrado.',
  forbidden: 'Sem permissão.',
}

const BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })
const NUM = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** "6890.00" → "R$ 6.890,00" (espaço comum, não o fino do Intl). */
export function formatBRL(v: string | number | null | undefined): string {
  const n = Number(v)
  if (v == null || v === '' || !Number.isFinite(n)) return '—'
  return BRL.format(n).replace(/\s/g, ' ')
}

/** "6890.00" → "6.890,00" — o que o campo Valor mostra ao editar. */
export function valorParaCampo(v: string | number | null | undefined): string {
  const n = Number(v)
  if (v == null || v === '' || !Number.isFinite(n)) return ''
  return NUM.format(n)
}

/**
 * Valor digitado no padrão brasileiro → texto que a API recebe ("6890.00").
 * Aceita "6.890,00", "6890,5", "6890.50", "6.890" (milhar) e "R$ 10".
 * Devolve `{ erro }` em vez de adivinhar quando o texto é ambíguo ou inválido.
 */
export function lerValor(texto: string | null | undefined): { valor: string } | { erro: string } {
  const s = String(texto ?? '').replace(/R\$/i, '').replace(/\s/g, '')
  if (!s) return { erro: 'Informe o valor.' }
  if (s.startsWith('-')) return { erro: 'O valor deve ser maior que zero.' }
  let inteiro: string
  let centavos = ''
  let m: RegExpMatchArray | null
  if ((m = s.match(/^(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?$/))) {
    inteiro = m[1].replace(/\./g, '')
    centavos = m[2] ?? ''
  } else if ((m = s.match(/^(\d+)\.(\d{1,2})$/))) {
    inteiro = m[1]
    centavos = m[2]
  } else {
    return { erro: 'Valor inválido. Use o formato 6.890,00.' }
  }
  if (centavos.length > 2) return { erro: 'Use no máximo duas casas decimais.' }
  inteiro = inteiro.replace(/^0+(?=\d)/, '')
  if (inteiro.length > 13) return { erro: 'Valor alto demais.' }
  const valor = `${inteiro}.${centavos.padEnd(2, '0')}`
  if (Number(valor) <= 0) return { erro: 'O valor deve ser maior que zero.' }
  return { valor }
}

const MSG_PYDANTIC: [RegExp, string][] = [
  [/at least 1 character/i, 'Campo obrigatório.'],
  [/at least 3 characters/i, 'Mínimo de 3 caracteres.'],
  [/at most 20 characters/i, 'Máximo de 20 caracteres.'],
  [/at most 200 characters/i, 'Máximo de 200 caracteres.'],
  [/greater than 0/i, 'O valor deve ser maior que zero.'],
  [/decimal places/i, 'Use no máximo duas casas decimais.'],
  [/field required/i, 'Campo obrigatório.'],
]

/**
 * Erro da API → mensagem por campo (aparece embaixo de cada campo) e uma
 * geral quando o erro não é de campo. 422 do Pydantic vem com `loc`; os
 * nossos vêm com `detail.campo`.
 */
export function errosDaApi(e: any): { campos: Record<string, string>; geral: string } {
  const det = e?.data?.detail
  const campos: Record<string, string> = {}
  if (Array.isArray(det)) {
    for (const x of det) {
      const campo = Array.isArray(x?.loc) ? String(x.loc[x.loc.length - 1]) : ''
      const msg = String(x?.msg || '')
      const pt = MSG_PYDANTIC.find(([re]) => re.test(msg))?.[1] || msg
      if (campo && !campos[campo]) campos[campo] = pt
    }
    return { campos, geral: Object.keys(campos).length ? '' : 'Dados inválidos.' }
  }
  const code = det?.code
  let msg = (code && IMOBILIZADO_ERROS[code]) || code || e?.message || 'erro'
  if (code === 'numero_duplicado' && det?.numero) msg = `Já existe um item com o número ${det.numero}.`
  if (det?.campo) {
    campos[det.campo] = msg
    return { campos, geral: '' }
  }
  return { campos, geral: msg }
}

/** Texto de uma linha do histórico ("R$ 6.890,00 → R$ 6.500,00"). */
export function textoHistorico(campo: string, v: string | null): string {
  if (v == null || v === '') return '—'
  if (campo === 'valor') return formatBRL(v)
  if (campo === 'status') return STATUS_LABELS[v] || v
  return v
}

/** Filtro do campo Responsável: busca por nome sem acento nem caixa. */
export function filtrarPessoas(pessoas: Pessoa[], termo: string): Pessoa[] {
  const t = normalizar(termo)
  return t ? pessoas.filter(p => normalizar(p.nome).includes(t)) : pessoas
}

function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
}
