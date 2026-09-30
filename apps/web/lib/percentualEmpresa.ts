// Porcentagem da empresa (Cadastros › Empresas).
//
// Eduardo, 29/09/2026: "em cadastros na aba empresas, precisamos colocar uma
// nova coluna, porcentagem, que vai ser a porcentagem de cada empresa que
// temos". É o % PADRÃO das notas de serviço de percentual que a empresa emite:
// a Emissão de Serviço usa este número quando a nota fixa não tem % própria.
// A API guarda NUMERIC(9,4) e devolve "0.5000"; a tela mostra "0,5%" (fmtPct).

export const PCT_EMPRESA_DICA = '% padrão das notas de serviço de percentual'

// Mesmas frases do servidor (schemas/nfse.py › checar_percentual).
const PCT_NAO_NUMERO = 'Digite só o número da porcentagem (ex.: 0,5).'
const PCT_ZERO = 'A porcentagem tem que ser maior que zero.'
const PCT_ACIMA = 'A porcentagem vai no máximo até 100%.'
const PCT_CASAS = 'A porcentagem aceita até 4 casas depois da vírgula (ex.: 0,5 ou 0,1234).'

// "0.5000" → "0,5": o que aparece no campo para editar. Vazio → "".
export function pctParaCampo(v: string | number | null | undefined): string {
  if (v == null || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toLocaleString('pt-BR', { maximumFractionDigits: 4, useGrouping: false })
}

// "0.5000" → "0.5": para comparar o que estava com o que foi digitado.
export function pctNormal(v: string | number | null | undefined): string {
  if (v == null || v === '') return ''
  const n = Number(v)
  return Number.isFinite(n) ? String(n) : String(v)
}

export type LeituraPct = { ok: true; valor: string } | { ok: false; erro: string }

// O que a pessoa digitou ("0,5", "0.5", "0,5 %") → "0.5", o formato que a API
// aceita. valor "" = sem porcentagem (apagar). Mais de 0 e até 100, até 4
// casas — a mesma regra do servidor, para o erro aparecer antes de salvar.
export function lerPctEmpresa(texto: string | null | undefined): LeituraPct {
  let s = (texto || '').replace(/[%\s]/g, '')
  if (!s) return { ok: true, valor: '' }
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  // "5," e ",5" também valem (5 e 0,5).
  if (!/^(\d+\.?\d*|\.\d+)$/.test(s)) return { ok: false, erro: PCT_NAO_NUMERO }
  const n = Number(s)
  if (!(n > 0)) return { ok: false, erro: PCT_ZERO }
  if (n > 100) return { ok: false, erro: PCT_ACIMA }
  const casas = (s.split('.')[1] || '').replace(/0+$/, '')
  if (casas.length > 4) return { ok: false, erro: PCT_CASAS }
  return { ok: true, valor: String(n) }
}
