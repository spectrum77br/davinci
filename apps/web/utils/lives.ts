// Marketing › Lives (03/11/2026): tipos e helpers puros da aba.
// Os helpers ficam entre os marcadores pro teste (tests/marketing-lives-sfc.cjs)
// conseguir rodá-los sem Nuxt.

export type LiveStatus = 'aguardando' | 'gravando' | 'gravada' | 'erro'

export type LiveMetricas = {
  espectadores_pico?: number | null
  espectadores_pico_em_s?: number | null
  espectadores_media?: number | null
  curtidas?: number | null
  comentarios?: number | null
  total_espectadores?: number | null
  compartilhamentos?: number | null
  seguidores_ganhos?: number | null
  serie?: [number, number | null, number | null][]
  produtos?: unknown[]
  partes?: number
}

export type Live = {
  id: string
  plataforma: 'shopee' | 'tiktok'
  link: string
  apresentador: string | null
  integration_id: string | null
  loja: string | null
  produtos: string | null
  observacao: string | null
  status: LiveStatus
  titulo: string | null
  canal: string | null
  sala_id: string | null
  inicio_em: string | null
  fim_em: string | null
  duracao_s: number | null
  arquivo: string | null
  tamanho_bytes: number | null
  metricas: LiveMetricas | null
  erro: string | null
  robo_visto_em: string | null
  created_at: string
  created_by_nome: string | null
}

export type LojaLive = { id: string; nome: string; plataforma: 'shopee' | 'tiktok' }

export const ERROS_LIVES: Record<string, string> = {
  link_invalido: 'O link precisa ser de uma live da Shopee (live.shopee.com.br, s.shopee.com.br, shp.ee) ou do TikTok (tiktok.com).',
  loja_nao_encontrada: 'Loja não encontrada',
  loja_de_outra_plataforma: 'A loja escolhida é de outra plataforma que não a do link',
  live_ja_comecou: 'A live já começou a ser gravada: o link não pode mais mudar',
  live_nao_deu_erro: 'Só dá pra mandar gravar de novo uma live que deu erro',
  live_nao_apagavel: 'Live gravando ou gravada não pode ser apagada',
  live_nao_encontrada: 'Live não encontrada',
}

// ---------- helpers puros

export function plataformaDoLink(link: string): 'shopee' | 'tiktok' | null {
  let u: URL
  try {
    u = new URL(link.trim())
  } catch {
    return null
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
  const h = u.hostname.toLowerCase()
  const de = (base: string) => h === base || h.endsWith('.' + base)
  if (de('shopee.com.br') || de('shp.ee')) return 'shopee'
  if (de('tiktok.com')) return 'tiktok'
  return null
}

export function duracaoTxt(s: number | null | undefined): string {
  if (s == null) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`
  if (m) return `${m} min`
  return `${s} s`
}

export function tamanhoTxt(b: number | null | undefined): string {
  if (b == null) return '—'
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(1).replace('.', ',')} GB`
  if (b >= 1024 ** 2) return `${Math.round(b / 1024 ** 2)} MB`
  return `${Math.round(b / 1024)} KB`
}

/** Número em pt-BR; ausente é travessão, nunca 0. */
export function numTxt(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('pt-BR')
}

/** Minutos desde o último sinal do robô (null se nunca deu sinal). */
export function minutosSemSinal(visto: string | null, agora: number = Date.now()): number | null {
  if (!visto) return null
  return Math.max(0, Math.floor((agora - new Date(visto).getTime()) / 60000))
}

/** Pontos de um polyline SVG (largura x altura) a partir da série de espectadores.
 *  Escala do menor ao maior valor: 6.000 → 6.400 espectadores vira linha reta
 *  se a escala começar no zero. Série plana fica no meio. */
export function sparkline(serie: LiveMetricas['serie'], largura = 160, altura = 32): string {
  const pts = (serie || []).filter((p) => typeof p[1] === 'number') as [number, number, unknown][]
  if (pts.length < 2) return ''
  const t0 = pts[0][0]
  const t1 = pts[pts.length - 1][0]
  const vals = pts.map((p) => p[1])
  const min = Math.min(...vals)
  const faixa = Math.max(...vals) - min
  return pts
    .map((p) => {
      const x = t1 === t0 ? 0 : ((p[0] - t0) / (t1 - t0)) * largura
      const y = faixa ? altura - 1 - ((p[1] - min) / faixa) * (altura - 2) : altura / 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

// ---------- fim helpers puros
