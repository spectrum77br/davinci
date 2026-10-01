// Denúncia (30/09/2026) — ajudantes das três telas (Anúncios, Denúncias,
// Casos). Os dados vêm do sistema de Fiscalização do Mac mini da Makisa, que
// guarda data/hora como texto no horário de Brasília ("2026-09-30 00:11:06")
// — por isso aqui é só recortar o texto, sem fuso.

export type Prova = {
  id: number
  anuncio_id: string | null
  caso_id: number | null
  denuncia_id: number | null
  tipo: string | null
  nome: string
  mime: string | null
  tamanho: number | null
  enviado_em: string | null
  enviado_por: string | null
  obs: string | null
  // Os arquivos das provas ficam no MEGA (conta sac@makisa), não no DaVinci.
  mega_caminho: string | null
  mega_em: string | null
  tem_arquivo: boolean
}

// Uma linha da sub-aba "Denúncias enviadas" (mesmo canal + protocolo = uma denúncia).
export type DenunciaEnviada = {
  id: number
  canal: string | null
  protocolo: string | null
  data: string | null
  hora: string | null
  situacao: string | null
  resultado: string | null
  prazo: string | null
  tipo: string | null
  tentativa: number | null
  refazer: number | null
  anuncios: { id: string | null; loja: string | null; titulo: string | null; situacao: string | null }[]
}

export type Resumo = {
  ultimo_envio_em: string | null
  anuncios: number
  denuncias: number
  casos: number
  provas: number
  provas_fora_do_mega: number
}

/** "2026-09-30 00:11:06" → "30/09/26 00:11"; "2026-09-30" → "30/09/26". */
export function dataBr(v: string | null | undefined, comHora = true): string {
  if (!v) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(String(v))
  if (!m) return String(v)
  const dia = `${m[3]}/${m[2]}/${m[1].slice(2)}`
  return comHora && m[4] ? `${dia} ${m[4]}:${m[5]}` : dia
}

/** Hoje em Brasília como "AAAA-MM-DD" (pra comparar com prazo). */
export function hojeBr(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())
}

export function numero(v: number | null | undefined): string {
  return (v ?? 0).toLocaleString('pt-BR')
}

export function dinheiro(v: number | null | undefined): string {
  if (v === null || v === undefined || v === ('' as any)) return '—'
  return Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

export function tamanho(bytes: number | null | undefined): string {
  if (!bytes) return ''
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1048576).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MB`
}

/** Há quanto tempo o mini mandou a última cópia ("há 3 min"). */
export function haQuanto(iso: string | null | undefined): string {
  if (!iso) return 'nunca'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 90) return 'agora há pouco'
  if (s < 3600) return `há ${Math.round(s / 60)} min`
  if (s < 86400 * 2) return `há ${Math.round(s / 3600)} h`
  return `há ${Math.round(s / 86400)} dias`
}

/** Cópia velha (> 30 min) — o mini pode estar desligado. */
export function copiaAtrasada(iso: string | null | undefined): boolean {
  return !iso || Date.now() - new Date(iso).getTime() > 30 * 60 * 1000
}

// Anúncio no ar é o problema (ainda vendendo); fora do ar é a vitória.
export function pillSituacaoAnuncio(s: string | null | undefined): string {
  if (s === 'ativo') return 'pill-warning'
  if (s === 'fora do ar') return 'pill-success'
  return 'pill-muted'
}

// Vinicius, 01/10/2026: na tela a coluna "Grupo" vira "Certificado" — GRUPO 1
// (o anúncio usa a nossa homologação Anatel ou a nossa certificação Inmetro)
// é "Nosso"; GRUPO 2 (nº de outra empresa, sem nº ou nº inválido) é
// "Diversos". O sistema do mini e o robô continuam com GRUPO 1/2 por dentro.
const NOME_GRUPO: Record<string, string> = {
  'GRUPO 1': 'Nosso',
  'GRUPO 2': 'Diversos',
  VERIFICAR: 'Verificar',
  DESCARTADO: 'Descartado',
  'FORA DE ESCOPO': 'Fora de escopo',
}

export function nomeGrupo(g: string | null | undefined): string {
  if (!g) return '—'
  return NOME_GRUPO[g] || g
}

// GRUPO 1 = terceiro declarando a NOSSA certificação (o mais grave).
export function pillGrupo(g: string | null | undefined): string {
  if (g === 'GRUPO 1') return 'pill-danger'
  if (g === 'GRUPO 2') return 'pill-warning'
  if (g === 'VERIFICAR') return 'pill-info'
  return 'pill-muted'
}

export function pillSituacaoDenuncia(s: string | null | undefined): string {
  switch (s) {
    case 'Procedente':
      return 'pill-success'
    case 'Improcedente':
      return 'pill-danger'
    case 'Em análise':
    case 'Sem resposta':
      return 'pill-warning'
    case 'Enviada':
      return 'pill-info'
    default:
      return 'pill-muted'
  }
}

const RESOLVEM = ['Anúncio removido', 'Anúncio ajustado', 'Loja suspensa']

export function pillResultado(r: string | null | undefined): string {
  if (!r) return 'pill-muted'
  if (RESOLVEM.includes(r)) return 'pill-success'
  if (r === 'Improcedente') return 'pill-danger'
  if (r.startsWith('Sem resposta')) return 'pill-warning'
  return 'pill-muted'
}

const ABERTAS = ['Pendente', 'Enviada', 'Em análise']

/** Prazo passou e a denúncia ainda não teve resposta. */
export function prazoVencido(prazo: string | null | undefined, situacao: string | null | undefined): boolean {
  return !!prazo && ABERTAS.includes(situacao || '') && prazo.slice(0, 10) < hojeBr()
}

export function pillStatusCaso(s: string | null | undefined): string {
  switch (s) {
    case 'Aberto':
      return 'pill-info'
    case 'Aguardando produto':
      return 'pill-warning'
    case 'Com jurídico':
      return 'pill-success'
    case 'Ajuizado':
      return 'pill-danger'
    default:
      return 'pill-muted'
  }
}

export function pillStatusCompra(s: string | null | undefined): string {
  if (s === 'Recebido') return 'pill-success'
  if (s === 'Aguardando entrega') return 'pill-warning'
  return 'pill-muted'
}

export function urlProva(id: number, baixar = false): string {
  return `/api/denuncia/provas/${id}/arquivo${baixar ? '?baixar=1' : ''}`
}
