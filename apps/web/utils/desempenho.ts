// Marketing › Desempenho — tipos da resposta de GET /api/marketing/metricas
// (versão 2) e os helpers puros das três telas que a usam.
//
// Os helpers saíram do SFC (Eduardo, 24/09/2026) porque agora são três
// componentes lendo os mesmos números: a tela (MarketingDesempenho), a tabela
// "qual vídeo rendeu mais" (MarketingDesempenhoVideos) e a de "onde vale
// investir" (MarketingDesempenhoInvestir). Uma regra de "—" ou de fuso em cada
// um acabaria divergindo.
//
// O teste (tests/marketing-desempenho-sfc.cjs) recorta o trecho entre os
// marcadores e roda de verdade, sem nada em volta. Por isso o que está lá
// dentro é autossuficiente: nenhum import e nenhuma classe do Tailwind — o
// Tailwind não varre utils/, então classe escrita aqui sairia sem CSS. Os
// helpers devolvem o SIGNIFICADO ('alto', 'acima', 'velha') e cada componente
// traduz pra cor.

export type Metrica = 'views' | 'curtidas' | 'comentarios' | 'compartilhamentos' | 'salvamentos' | 'alcance'
export type Numeros = Partial<Record<Metrica, number>>
export type Leitura = 'pouco_dado' | 'indicio' | 'comparavel'
export type Dimensao = 'produto' | 'formato' | 'agencia' | 'roteiro' | 'horario'

export type PostagemDesempenho = {
  postagem_id: string
  creative_id: string
  marca_id: string | null
  marca: string
  plataforma: string
  conta: string | null
  post_url: string | null
  publicado_em: string
  origem: string | null
  titulo: string
  horario: '12h' | '19h' | 'outro'
  idade_horas: number
  estado: 'ok' | 'aguardando' | 'falhou'
  lido_em: string | null
  tentado_em: string | null
  erro: string | null
  acumulado: Numeros
  no_periodo: Numeros
  no_periodo_estimado: boolean
  views_marco: number | null
  marco_motivo: 'aguardando' | 'sem_views' | 'cedo' | 'buraco' | null
  marco_pronto_em: string | null
  indice_views: number | null
  indice_motivo: 'sem_marco' | 'base_pequena' | 'base_zero' | null
  base: { mediana: number | null; n: number }
  taxa_interacao: number | null
  indice_interacao: number | null
  ritmo_dia: number | null
  curva: [number, number][]
  autor_diferente: string | null
}

export type Rotulado = { chave: string; rotulo: string }

export type CriativoDesempenho = {
  creative_id: string
  titulo: string
  marca: string | null
  marca_id: string | null
  sku: string | null
  modelo: string | null
  produto: Rotulado
  formato: Rotulado
  agencia: Rotulado
  roteiro: Rotulado
  primeira_publicacao_em: string | null
  indice_views: number | null
  indice_interacao: number | null
  n_indices: number
  /** plataforma → ids das postagens, a mais nova primeiro. */
  postagens: Record<string, string[]>
}

export type GrupoDesempenho = {
  chave: string
  rotulo: string
  unidade: 'criativo' | 'postagem'
  total: number
  n: number
  indice_views: number | null
  indice_interacao: number | null
  leitura: Leitura
  por_rede: Record<string, { mediana: number | null; n: number }>
  melhor: { creative_id: string; postagem_id: string | null; titulo: string; indice_views: number } | null
}

export type RedeResumo = {
  plataforma: string
  videos: number
  aguardando: number
  com_falha: number
  metrica_serie: 'views' | 'curtidas'
  sem_views: boolean
  no_periodo: Numeros
  interacoes_no_periodo: number | null
  acumulado: Numeros
  // A porcentagem do cartão: os `dias` fechados até `ate` contra os `dias`
  // antes deles. `anterior` nulo = a leitura ainda não existia naquela época.
  comparacao?: { atual: number | null; anterior: number | null; ate: string }
  periodo_anterior: number | null
  serie: (number | null)[]
  estimado_dias: string[]
  lido_em: string | null
  erro: string | null
}

export type VideoMarca = {
  postagem_id: string
  post_url: string | null
  publicado_em: string | null
  titulo: string
  acumulado: Numeros
  no_periodo: Numeros
  coletado_em: string | null
  removido: boolean
  erro: string | null
  estado?: string
}

export type PlataformaMarca = {
  plataforma: string
  posts: number
  aguardando?: number
  acumulado: Numeros
  no_periodo: Numeros
  coletado_em: string | null
  lido_em?: string | null
  erro: string | null
  videos: VideoMarca[]
}

export type LinhaMarca = {
  marca: string
  marca_id?: string | null
  posts: number
  aguardando?: number
  acumulado: Numeros
  no_periodo: Numeros
  plataformas: PlataformaMarca[]
}

export type Minimos = {
  base_conta: number
  views_taxa: number
  indicio: number
  comparavel: number
  tolerancia_h: number
}

export type Coleta = {
  ultima_leitura_em: string | null
  proxima_leitura_em: string | null
  proxima_noturna_em: string | null
  inicio_da_coleta: string | null
  em_andamento: boolean
  pode_atualizar_em: string | null
  ultima_rodada: {
    modo: string
    inicio: string
    fim: string
    total: number
    ok: number
    falhou: number
  } | null
}

export type RespostaDesempenho = {
  versao: number
  dias: number
  desde: string
  ate: string
  marco: number
  marca_id: string | null
  gerado_em: string
  minimos: Minimos
  coleta: Coleta
  marcas_disponiveis: { id: string; nome: string }[]
  resumo: {
    videos: { no_ar: number; aguardando: number; com_falha: number; fora_do_ar: number; fora_do_desempenho: number }
    redes: RedeResumo[]
    tendencia: { views_no_periodo: number | null; redes_sem_views: string[] }
  }
  serie_dias: string[]
  publicacoes_por_dia: number[]
  postagens: PostagemDesempenho[]
  criativos: CriativoDesempenho[]
  grupos: Record<Dimensao, GrupoDesempenho[]>
  marcas: LinhaMarca[]
  sem_video_no_ar: string[]
  fora_do_ar: { postagem_id: string; creative_id?: string; marca: string; plataforma: string; post_url: string | null; publicado_em: string | null }[]
  fora_do_desempenho: {
    postagem_id: string
    marca: string
    plataforma: string
    conta: string | null
    post_url: string | null
    publicado_em: string | null
    motivo: string | null
    em: string | null
  }[]
}

export type Celula = {
  estado: 'nao_postado' | 'apagado' | 'aguardando' | 'cedo' | 'sem_views' | 'buraco' | 'falhou' | 'ok'
  texto: string
  detalhe: string
  /** Texto do title (passar o mouse). */
  dica: string
  /** A leitura de hoje falhou e a célula mostra a anterior — dito em âmbar. */
  aviso: string
}

// ---------- helpers puros (travados em tests/marketing-desempenho-sfc.cjs)

// A mesma ordem em que a API manda `resumo.redes`: cartão, coluna e filtro
// nunca trocam de lugar entre si. O Facebook quase não recebe post — só ganha
// coluna quando houver um (redesDaTabela).
export const ORDEM_REDES = ['instagram', 'youtube', 'tiktok', 'facebook']
export const ROTULO_REDE: Record<string, string> = {
  instagram: 'Instagram', youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook',
}
// Sigla do cartão de vídeo no celular, onde "● Instagram" não cabe três vezes.
export const SIGLA_REDE: Record<string, string> = {
  instagram: 'IG', youtube: 'YT', tiktok: 'TT', facebook: 'FB',
}

/** Cor da rede. É variável CSS (definida na raiz `.desempenho`) pra trocar no modo escuro. */
export function corRede(plataforma: string): string {
  return `var(--rede-${plataforma}, currentColor)`
}

/** Colunas das tabelas: as três redes de sempre, e o Facebook só se alguém postou lá. */
export function redesDaTabela(presentes: string[]): string[] {
  return ORDEM_REDES.filter((r) => r !== 'facebook' || presentes.includes('facebook'))
}

/** Número da tela: ausente vira "—", nunca 0 (decisão 1 do topo de MarketingDesempenho.vue). */
export function num(n: Numeros, chave: string): string {
  const v = (n as any)[chave]
  return v === undefined || v === null ? '—' : v.toLocaleString('pt-BR')
}
export function ganho(n: Numeros, chave: string): string {
  const v = (n as any)[chave]
  if (v === undefined || v === null || v === 0) return ''
  return v > 0 ? `+${v.toLocaleString('pt-BR')}` : v.toLocaleString('pt-BR')
}

/** Ganho com sinal por extenso: "+1.234", "-5", "0"; ausente vira "—". */
export function sinal(v: number | null | undefined): string {
  if (v === undefined || v === null) return '—'
  return v > 0 ? `+${v.toLocaleString('pt-BR')}` : v.toLocaleString('pt-BR')
}

/** "12,3 mil" — o título do cartão precisa caber numa linha no celular. */
export function compacto(v: number | null | undefined): string {
  if (v === undefined || v === null) return '—'
  return new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

/** Ganho com sinal: "+12,3 mil"; negativo sai com o "-" do próprio número. */
export function maisCompacto(v: number | null | undefined): string {
  if (v === undefined || v === null) return '—'
  return v >= 0 ? `+${compacto(v)}` : compacto(v)
}

/** "3 vídeos", "1 vídeo" — a tela antiga escrevia "vídeo(s)". */
export function qtd(n: number, um: string, varios: string): string {
  return `${n.toLocaleString('pt-BR')} ${n === 1 ? um : varios}`
}

/** Índice "× o normal da conta": 2,1×. */
export function fmtIndice(v: number | null | undefined): string {
  if (v === undefined || v === null) return '—'
  return `${v.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}×`
}

/**
 * Tom do chip do índice. Não existe "ruim": vídeo fraco é informação, não
 * erro — vermelho aqui faria o Eduardo cortar produto por causa de um post.
 */
export function tomIndice(v: number | null | undefined): 'alto' | 'normal' | 'baixo' | null {
  if (v === undefined || v === null) return null
  if (v >= 1.25) return 'alto'
  if (v < 0.8) return 'baixo'
  return 'normal'
}

/**
 * Barra divergente de "vs. o normal da conta". Escala log2 centrada em 1×:
 * 2× e 0,5× têm o mesmo tamanho (dobro e metade são a mesma distância do
 * normal). Presa entre 0,25× e 4× pra um viral não achatar o resto.
 * `pct` é a porcentagem da largura TOTAL (o lado vai até 50).
 */
export function barra(
  v: number | null | undefined,
  leitura?: Leitura | null,
): { lado: 'direita' | 'esquerda' | 'centro'; pct: number; tom: 'acima' | 'normal' | 'abaixo' | 'cinza' } | null {
  if (v === undefined || v === null) return null
  const lado = v > 1 ? 'direita' : v < 1 ? 'esquerda' : 'centro'
  const pct = Math.round((Math.min(Math.abs(Math.log2(Math.max(v, 0.25))), 2) / 2) * 50 * 10) / 10
  // Pouco dado fica cinza mesmo lá em cima: dois vídeos com sorte não são
  // um produto campeão, e verde ali seria um veredito que o dado não dá.
  const tom = leitura === 'pouco_dado' ? 'cinza' : v >= 1.25 ? 'acima' : v <= 0.8 ? 'abaixo' : 'normal'
  return { lado, pct, tom }
}

export function rotuloLeitura(l: string | null | undefined): string {
  if (l === 'pouco_dado') return 'pouco dado — não conclua ainda'
  if (l === 'indicio') return 'indício'
  if (l === 'comparavel') return 'dá pra comparar'
  return ''
}

/** Taxa de interação: 0,078 → "7,8%". */
export function pct(t: number | null | undefined): string {
  if (t === undefined || t === null) return '—'
  return `${(t * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`
}

/**
 * Variação contra o período anterior. Sem base (ou base zero/negativa) não há
 * porcentagem honesta — devolve null e a tela diz que ainda não tem base.
 */
export function deltaTxt(
  atual: number | null | undefined,
  anterior: number | null | undefined,
): { texto: string; sobe: boolean } | null {
  if (atual === undefined || atual === null || anterior === undefined || anterior === null) return null
  if (anterior <= 0) return null
  const sobe = atual >= anterior
  const p = Math.round((Math.abs(atual - anterior) / anterior) * 100)
  return { texto: `${sobe ? '▲' : '▼'} ${p}%`, sobe }
}

// Códigos de erro do PATCH .../desempenho (tirar / voltar a contar).
export const ERROS_DESEMPENHO: Record<string, string> = {
  motivo_obrigatorio: 'Escreva o motivo (pelo menos 3 letras).',
  fora_da_sua_equipe: 'Esse vídeo é de outra equipe de marketing.',
  nao_publicada: 'Essa postagem não está publicada.',
  not_found: 'Essa postagem não existe mais.',
  forbidden: 'Você não tem permissão pra isso.',
}

// Tudo em horário de Brasília: o servidor manda UTC, e "hoje" pro Eduardo é o
// dia de São Paulo — às 22h o UTC já virou o dia seguinte.
const FMT_BRT = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Sao_Paulo',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
function paraData(x: string | number | Date): Date {
  return x instanceof Date ? x : new Date(x)
}
function partesBRT(x: string | number | Date): { dia: string; hora: string } {
  const p: Record<string, string> = {}
  for (const parte of FMT_BRT.formatToParts(paraData(x))) p[parte.type] = parte.value
  return { dia: `${p.year}-${p.month}-${p.day}`, hora: `${p.hour}:${p.minute}` }
}
/** Dias de calendário entre duas datas 'AAAA-MM-DD' (b − a). */
function diasEntre(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 864e5)
}
/** "12:47" em Brasília. */
export function hhmm(iso: string | null | undefined): string {
  return iso ? partesBRT(iso).hora : ''
}
/**
 * "26/08". Data pura ('2026-08-26', como vem `desde`) é cortada, não
 * convertida: `new Date('2026-08-26')` é meia-noite UTC, que em Brasília
 * ainda é o dia 25.
 */
export function ddmm(s: string | null | undefined): string {
  if (!s) return ''
  const dia = /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : partesBRT(s).dia
  return `${dia.slice(8, 10)}/${dia.slice(5, 7)}`
}
/** "hoje", "amanhã" ou "em 26/09" — pro "próxima leitura … às 23:47". */
export function diaRelativo(iso: string | null | undefined, agora: string | number | Date = new Date()): string {
  if (!iso) return ''
  const k = diasEntre(partesBRT(agora).dia, partesBRT(iso).dia)
  return k <= 0 ? 'hoje' : k === 1 ? 'amanhã' : `em ${ddmm(iso)}`
}

/**
 * Quando foi a leitura, e se ela está velha. Mais de 30 h quer dizer que a
 * leitura da noite (23:47) não rodou — número velho com cara de novo é o erro
 * que ninguém percebe (decisão 3 do topo de MarketingDesempenho.vue).
 */
export function frescor(
  iso: string | null | undefined,
  agora: string | number | Date = new Date(),
): { tom: 'nenhuma' | 'ok' | 'velha'; texto: string } {
  if (!iso) return { tom: 'nenhuma', texto: 'ainda sem leitura' }
  const lido = partesBRT(iso)
  const k = diasEntre(lido.dia, partesBRT(agora).dia)
  const texto = k === 0
    ? `hoje às ${lido.hora}`
    : k === 1 ? `ontem às ${lido.hora}` : `em ${ddmm(iso)} às ${lido.hora}`
  const horas = (paraData(agora).getTime() - paraData(iso).getTime()) / 36e5
  return { tom: horas > 30 ? 'velha' : 'ok', texto }
}

/**
 * Uma célula "rede × vídeo" da tabela de comparação. O número principal é
 * a view NA MESMA IDADE (D+marco), não o total: vídeo antigo não pode ganhar
 * só por ter tido mais tempo. Cada estado diz POR QUE não tem número, pra
 * "—" nunca ser ambíguo.
 */
export function celula(
  p: PostagemDesempenho | undefined | null,
  marco: number,
  agora: string | number | Date = new Date(),
  apagado = false,
): Celula {
  const vazio = { detalhe: '', dica: '', aviso: '' }
  // Saiu e foi apagado depois: "não postado" seria mentira (24/09/2026).
  if (!p && apagado) return { ...vazio, estado: 'apagado', texto: 'apagado da rede', dica: 'O vídeo saiu nesta rede e foi apagado depois — não conta mais.' }
  if (!p) return { ...vazio, estado: 'nao_postado', texto: 'não postado' }
  const dias = qtd(marco, 'dia', 'dias')
  if (p.estado === 'aguardando' || (p.estado === 'ok' && p.marco_motivo === 'aguardando')) {
    // Aguardar é o normal da primeira hora — cinza, nunca âmbar.
    // Post de outro dia ainda sem leitura (robô parado?) diz a data também —
    // "publicado às 12:04" de três dias atrás pareceria de hoje.
    let dica = ''
    if (p.publicado_em) {
      dica = diasEntre(partesBRT(p.publicado_em).dia, partesBRT(agora).dia) === 0
        ? `Publicado às ${hhmm(p.publicado_em)}. `
        : `Publicado em ${ddmm(p.publicado_em)} às ${hhmm(p.publicado_em)}. `
    }
    dica += 'A primeira leitura sai em até 1 hora'
    dica += p.marco_pronto_em
      ? `; a comparação de ${dias} fica pronta em ${ddmm(p.marco_pronto_em)}.`
      : '.'
    return { ...vazio, estado: 'aguardando', texto: 'aguardando 1ª leitura', dica }
  }
  // Falhou sem NENHUMA leitura boa antes: não há número pra mostrar.
  if (p.estado === 'falhou' && !p.lido_em) {
    return { ...vazio, estado: 'falhou', texto: 'não consegui ler', dica: p.erro || '' }
  }
  // Falhou HOJE mas já tinha lido antes: mostra a leitura boa e avisa.
  const aviso = p.estado === 'falhou' ? `a leitura de hoje falhou — mostrando a de ${ddmm(p.lido_em)}` : ''
  const acumulado = p.acumulado || {}
  const total = num(acumulado, 'views')
  if (p.views_marco !== null && p.views_marco !== undefined) {
    return { ...vazio, aviso, estado: 'ok', texto: p.views_marco.toLocaleString('pt-BR'), detalhe: `total ${total}` }
  }
  if (p.marco_motivo === 'cedo') {
    const k = p.marco_pronto_em ? diasEntre(partesBRT(agora).dia, partesBRT(p.marco_pronto_em).dia) : null
    const texto = k === null
      ? 'ainda cedo'
      : k <= 0 ? 'sai hoje à noite' : k === 1 ? 'falta 1 dia' : `faltam ${k} dias`
    return { ...vazio, aviso, estado: 'cedo', texto, detalhe: `total ${total} até agora` }
  }
  if (p.marco_motivo === 'sem_views') {
    return {
      ...vazio, aviso, estado: 'sem_views', texto: 'sem views',
      detalhe: `${num(acumulado, 'curtidas')} curtidas · a conta não liberou insights`,
    }
  }
  // 'buraco': a coleta pulou a idade do marco. NULL, não número inventado.
  return { ...vazio, aviso, estado: 'buraco', texto: 'sem leitura nessa idade', detalhe: `total ${total}` }
}

/**
 * Mini-barras do cartão da rede (viewBox W×H). Dia sem dado (null) não tem
 * barra — é diferente de dia com ganho zero, que tem barra de altura 0 e o
 * valor no title. Ganho negativo (o YouTube tira view) também desenha 0: a
 * barra mostra o que entrou, o sinal fica no title.
 */
export function geomBarras(
  serie: (number | null)[],
  W = 100,
  H = 28,
): { barras: { i: number; x: number; w: number; y: number; h: number; v: number }[]; yMax: number } {
  let yMax = 1
  for (const v of serie) if (v !== null && v !== undefined && v > yMax) yMax = v
  const n = serie.length
  if (!n) return { barras: [], yMax }
  const passo = W / n
  const w = 0.7 * passo
  const barras: { i: number; x: number; w: number; y: number; h: number; v: number }[] = []
  serie.forEach((v, i) => {
    if (v === null || v === undefined) return
    const h = v > 0 ? (v / yMax) * H : 0
    barras.push({ i, x: i * passo + (passo - w) / 2, w, y: H - h, h, v })
  })
  return { barras, yMax }
}

/**
 * Curva "views nos primeiros 14 dias" (pontos [dias, views], âncora 0,0
 * incluída), com as linhas tracejadas das idades comparadas (1d, 3d, 7d).
 */
export function geomCurva(
  curva: [number, number][],
  W = 160,
  H = 40,
): {
  W: number; H: number; PAD: { t: number; r: number; b: number; l: number }
  d: string; pontos: { x: number; y: number; v: number }[]; marcos: { x: number; rotulo: string }[]; yMax: number
} {
  const PAD = { t: 3, r: 4, b: 10, l: 4 }
  const DIAS = 14
  const iw = W - PAD.l - PAD.r
  const ih = H - PAD.t - PAD.b
  const pts = [...(curva || [])].filter((c) => c[0] >= 0 && c[0] <= DIAS).sort((a, b) => a[0] - b[0])
  let yMax = 1
  for (const [, v] of pts) if (v > yMax) yMax = v
  const pontos = pts.map(([d, v]) => ({
    x: PAD.l + (d / DIAS) * iw,
    y: PAD.t + ih - (Math.max(v, 0) / yMax) * ih,
    v,
  }))
  const d = pontos.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const marcos = [1, 3, 7].map((m) => ({ x: PAD.l + (m / DIAS) * iw, rotulo: `${m}d` }))
  return { W, H, PAD, d, pontos, marcos, yMax }
}

// ---------- fim helpers puros
