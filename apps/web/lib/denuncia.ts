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

// 01/10: o status do caso na tela sai dos fatos (API _status_caso): Aberto (sem compra) → Aguardando
// produto → Produto recebido → Com jurídico (enviado ao advogado) → Ajuizado / Encerrado
// 06/10: dá pra trocar à mão (a mesma lista da API STATUS_CASO) — vale por cima dos fatos
export const STATUS_CASO = ['Aberto', 'Aguardando produto', 'Produto recebido', 'Com jurídico', 'Ajuizado', 'Encerrado'] as const

export function pillStatusCaso(s: string | null | undefined): string {
  switch (s) {
    case 'Aberto':
      return 'pill-muted'
    case 'Aguardando produto':
      return 'pill-warning'
    case 'Produto recebido':
      return 'pill-info'
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

// ── aba "Anúncios e denúncias" (01/10/2026) — o que a loja fez com a nossa denúncia ("Na loja") e onde o
// anúncio está no caminho da Anatel ("Na Anatel"), calculados na API (services/denuncia_painel).
export type PainelCaso = { id: number; codigo: string | null; status: string | null }
// o que a ficha do anúncio conta para quem a abriu (título da gaveta)
export type InfoAnuncio = { titulo: string; subtitulo: string; grupo: string | null }
export type PainelStatus = {
  chave: string
  rotulo: string
  tom: string
  data?: string | null
  tentativas?: number
  protocolo?: string | null
  consumidor?: string | null
  // 07/10: situação na Anatel (na_fila, enviada, recebida, em_analise, respondida, complemento, encerrada, nada) e o
  // protocolo em que ela respondeu (Consumidor ou SEI) — o clique abre o texto da Anatel
  fase?: string
  resposta_protocolo?: string | null
  resposta_em?: string | null
}
export type PainelAnuncio = {
  id: string
  marketplace: string | null
  shop_id: string | null
  loja: string | null
  titulo: string | null
  url: string | null
  hom: string | null
  inmetro: string | null
  grupo: string | null
  vendas: number
  situacao: string | null
  visto_primeiro: string | null
  loja_st: PainelStatus
  anatel_st: PainelStatus
  casos: PainelCaso[]
  // 01/10: "criar" pedido ao mini e o caso ainda não chegou na cópia
  caso_pendente?: boolean
  nden: number
}
export type PainelLoja = {
  marketplace: string | null
  shop_id: string | null
  chave: string
  loja: string | null
  anuncios: number
  no_ar: number
  fora_do_ar: number
  vendas: number
  nosso: number
  diversos: number
  outros: number
  na_loja: Record<string, number>
  na_anatel: Record<string, number>
  anatel_fases: Record<string, number>
  processos: string[]
  casos: PainelCaso[]
  caso_pendente?: boolean
  ultimo_achado: string | null
}
// criar/excluir caso que o robô do mini não conseguiu fazer (últimas 24 h)
export type FalhaCaso = {
  id: number
  tipo: string
  texto: string
  resultado: string | null
  pedido_em: string
  por: string | null
}

// "9 removidos · 21 recusadas · 36 aguardando" — a ordem e o texto de cada etiqueta
export const ETIQ_LOJA: [string, string, string, string][] = [
  ['removido', 'pill-success', 'removido', 'removidos'],
  ['recusou', 'pill-danger', 'recusada', 'recusadas'],
  // 01/10: a loja já respondeu e o robô está conferindo o anúncio (vira recusada ou removido)
  ['conferindo', 'pill-muted', 'respondeu · conferindo', 'responderam · conferindo'],
  ['aguardando', 'pill-muted', 'aguardando', 'aguardando'],
  ['nao', 'pill-muted', 'sem denúncia', 'sem denúncia'],
]
export const ETIQ_ANATEL: [string, string, string, string][] = [
  ['processo', 'pill-info', 'com processo', 'com processo'],
  ['fila', 'pill-warning', 'na fila', 'na fila'],
  ['falta_print', 'pill-muted', 'na fila', 'na fila'],
  ['esperando_recusa', 'pill-muted', 'esperando recusa', 'esperando recusa'],
  ['falta_loja', 'pill-muted', 'falta denunciar na loja', 'falta denunciar na loja'],
]
// 07/10/2026 (Vinicius: "esse falta print ficou ruim… eu preciso saber assim, respondida, encerrada"): a coluna
// Anatel por loja conta pela situação NA ANATEL. O que pede ação vem primeiro.
export const ETIQ_ANATEL_FASE: [string, string, string, string][] = [
  ['complemento', 'pill-danger', 'pede complemento', 'pedem complemento'],
  ['respondida', 'pill-warning', 'respondida', 'respondidas'],
  ['em_analise', 'pill-info', 'em análise', 'em análise'],
  ['recebida', 'pill-info', 'recebida', 'recebidas'],
  ['enviada', 'pill-info', 'enviada', 'enviadas'],
  ['encerrada', 'pill-muted', 'encerrada', 'encerradas'],
  ['na_fila', 'pill-muted', 'na fila', 'na fila'],
]
export const FASE_COM_RESPOSTA = ['respondida', 'complemento']
export function etiquetas(cont: Record<string, number>, tabela: [string, string, string, string][]) {
  return tabela
    .filter(([k]) => cont[k])
    .map(([k, cls, um, varios]) => ({ k, cls, texto: `${cont[k]} ${cont[k] > 1 ? varios : um}` }))
}
const TOM_PILL: Record<string, string> = {
  success: 'pill-success', danger: 'pill-danger', warning: 'pill-warning', info: 'pill-info', muted: 'pill-muted',
}
export function pillTom(t: string | undefined): string {
  return TOM_PILL[t || 'muted'] || 'pill-muted'
}

// 01/10 (Vinicius): "troca 'no ar' pelo nome Ativo, sim ou não" (o robô grava "ativo" / "fora do ar")
export function ativoSimNao(s: string | null | undefined): string {
  if (s === 'ativo') return 'Sim'
  if (s === 'fora do ar') return 'Não'
  return '—'
}
export function pillAtivo(s: string | null | undefined): string {
  if (s === 'ativo') return 'pill-warning'
  if (s === 'fora do ar') return 'pill-success'
  return 'pill-muted'
}
