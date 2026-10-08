// Marketing › Desempenho — tipos da resposta de GET /api/marketing/metricas
// (versão 2) e os helpers puros das três telas que a usam.
//
// Os helpers saíram do SFC (Eduardo, 24/09/2026) porque são vários
// componentes lendo os mesmos números: a tela (MarketingDesempenho), os
// "vídeos mais vistos" (MarketingDesempenhoMaisVistos), "todos os vídeos"
// (MarketingDesempenhoVideos) e "onde vale investir"
// (MarketingDesempenhoInvestir). Uma regra de "—" ou de fuso em cada um
// acabaria divergindo.
//
// 06/10/2026: a tela fala em VIEWS SOMADAS (a última leitura de cada post,
// somada por vídeo e por rede), em janelas de semana e mês. O índice
// "× o normal da conta" saiu da tela — confundia mais do que ajudava. Em
// "Onde vale investir", as views da semana/mês são as GANHAS nesses dias por
// todos os vídeos do grupo (a mesma conta do Resumo); os "mais vistos" são os
// vídeos PUBLICADOS na janela, pelas views até hoje.
//
// O teste (tests/marketing-desempenho-sfc.cjs) recorta o trecho entre os
// marcadores e roda de verdade, sem nada em volta. Por isso o que está lá
// dentro é autossuficiente: nenhum import e nenhuma classe do Tailwind — o
// Tailwind não varre utils/, então classe escrita aqui sairia sem CSS. Os
// helpers devolvem o SIGNIFICADO ('alto', 'acima', 'velha') e cada componente
// traduz pra cor.

export type Metrica = 'views' | 'curtidas' | 'comentarios' | 'compartilhamentos' | 'salvamentos' | 'alcance'
export type Numeros = Partial<Record<Metrica, number>>
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

/** Views de um vídeo: a soma da última leitura de cada post dele. */
export type ViewsVideo = {
  /** Nulo = nenhum post com número ainda (aguardando, sem views). Nunca 0 por falta. */
  total: number | null
  /** Só as redes que deram número, na ordem das redes. */
  por_rede: Record<string, number>
  postagens: number
  com_numero: number
}

export type CriativoDesempenho = {
  creative_id: string
  /** Como o vídeo é reconhecido: o título da ideia, senão a 1ª linha da legenda. */
  nome: string
  /** 1ª linha da legenda. */
  titulo: string
  marca: string | null
  marca_id: string | null
  sku: string | null
  modelo: string | null
  /** Produto = APARELHO (sem cor nem tamanho); `skus`/`variantes` dizem o que o vídeo mostra. */
  produto: Rotulado & { skus?: string[]; variantes?: string[] }
  formato: Rotulado
  agencia: Rotulado
  roteiro: Rotulado
  primeira_publicacao_em: string | null
  /** Dias de calendário (Brasília) desde a 1ª publicação: 0 = hoje. */
  idade_dias: number | null
  views: ViewsVideo
  /** O post mais visto do vídeo (o link dele). */
  melhor_post: { plataforma: string; postagem_id: string; post_url: string | null; views: number } | null
  // O índice continua na API (contrato de 24/09); a tela não lê mais.
  indice_views?: number | null
  indice_interacao?: number | null
  n_indices?: number
  /** plataforma → ids das postagens, a mais nova primeiro. */
  postagens: Record<string, string[]>
}

export type JanelaGrupo = {
  /**
   * Views GANHAS na janela por todos os vídeos do grupo (o antigo que continua
   * rendendo conta) — a mesma conta do Resumo. Nulo = nenhum ganho medido.
   */
  views: number | null
  /** Vídeos (ou postagens, no Horário) publicados na janela. */
  videos: number
  /** Desses, quantos já têm views. */
  com_numero: number
  /** Views até hoje dos vídeos publicados na janela. */
  views_dos_publicados?: number | null
  /** views_dos_publicados ÷ com_numero: quanto rende um vídeo novo do grupo. */
  media: number | null
  mediana: number | null
}

export type GrupoDesempenho = {
  chave: string
  rotulo: string
  /** Produto: as cores/tamanhos e os SKUs que o aparelho juntou ("Laranja, Prata · SKU dg088.ci, dg089.ci"). */
  detalhe: string | null
  unidade: 'criativo' | 'postagem'
  semana: JanelaGrupo
  mes: JanelaGrupo
  /** Views ganhas no mês, por rede. */
  por_rede_mes: Record<string, number>
  /** O vídeo publicado no mês com mais views até hoje, com o link do post mais visto dele. */
  melhor: {
    creative_id: string
    postagem_id: string | null
    nome: string
    titulo: string
    views: number
    plataforma: string | null
    post_url: string | null
  } | null
}

export type Janelas = {
  semana: { dias: number; desde: string }
  mes: { dias: number; desde: string }
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
  janelas: Janelas
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
  estado: 'nao_postado' | 'apagado' | 'aguardando' | 'sem_views' | 'falhou' | 'ok'
  texto: string
  detalhe: string
  /** Texto do title (passar o mouse). */
  dica: string
  /** A leitura de hoje falhou e a célula mostra a anterior — dito em âmbar. */
  aviso: string
}

export type Janela = 'semana' | 'mes'
export type OrdemGrupos = 'servidor' | 'mes' | 'semana' | 'por_video'

// ---------- helpers puros (travados em tests/marketing-desempenho-sfc.cjs)

// A mesma ordem em que a API manda `resumo.redes`: cartão, coluna e filtro
// nunca trocam de lugar entre si. O Facebook quase não recebe post — só ganha
// coluna quando houver um (redesDaTabela). A Shopee Vídeo (08/10/2026) idem:
// as views dela entram no total do vídeo, então a coluna tem de existir
// quando há post lá — senão o total não bate com a soma das colunas.
export const ORDEM_REDES = ['instagram', 'youtube', 'tiktok', 'facebook', 'shopee']
// Redes que só ganham coluna quando alguém postou lá.
const REDES_SO_COM_POST = ['facebook', 'shopee']
export const ROTULO_REDE: Record<string, string> = {
  instagram: 'Instagram', youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook', shopee: 'Shopee',
}
// Sigla do cartão de vídeo no celular, onde "● Instagram" não cabe três vezes.
export const SIGLA_REDE: Record<string, string> = {
  instagram: 'IG', youtube: 'YT', tiktok: 'TT', facebook: 'FB', shopee: 'SH',
}

/** Cor da rede. É variável CSS (definida na raiz `.desempenho`) pra trocar no modo escuro. */
export function corRede(plataforma: string): string {
  return `var(--rede-${plataforma}, currentColor)`
}

/** Colunas das tabelas: as três redes de sempre, e o Facebook/a Shopee só se alguém postou lá. */
export function redesDaTabela(presentes: string[]): string[] {
  return ORDEM_REDES.filter((r) => !REDES_SO_COM_POST.includes(r) || presentes.includes(r))
}

/** Quantas colunas de rede a grade monta (3, 4 ou 5) — a chave das classes de grade. */
export function colunasDaGrade(redes: string[]): 3 | 4 | 5 {
  return redes.length >= 5 ? 5 : redes.length === 4 ? 4 : 3
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

/** Views da tela: "8.362"; nulo (a rede ainda não deu número) vira "—", nunca 0. */
export function fmtViews(v: number | null | undefined): string {
  if (v === undefined || v === null) return '—'
  return v.toLocaleString('pt-BR')
}

// Semana = hoje e os 6 dias antes; mês = hoje e os 29 antes (dias de
// calendário em Brasília, como `idade_dias` vem da API).
export const DIAS_JANELA: Record<string, number> = { semana: 7, mes: 30 }

/**
 * Os vídeos mais vistos publicados na janela (pela 1ª publicação). Vídeo
 * ainda sem número fica de fora — não é "0 views". Empate: o mais novo.
 */
export function maisVistos<T extends { idade_dias: number | null; views: { total: number | null }; primeira_publicacao_em: string | null }>(
  criativos: T[],
  janela: string,
  n = 10,
): T[] {
  const limite = (DIAS_JANELA[janela] ?? 30) - 1
  return (criativos || [])
    .filter((c) => c.idade_dias !== null && c.idade_dias !== undefined && c.idade_dias <= limite)
    .filter((c) => c.views && c.views.total !== null && c.views.total !== undefined)
    .sort((a, b) => ((b.views.total as number) - (a.views.total as number))
      || ((Date.parse(b.primeira_publicacao_em || '') || 0) - (Date.parse(a.primeira_publicacao_em || '') || 0)))
    .slice(0, n)
}

/**
 * Ordem de "Onde vale investir". 'servidor' mantém a do servidor (views no
 * mês); as outras ordenam aqui. Sem número vai pro fim, e "(sem produto)",
 * "(sem agência)"… sempre por último: eles juntam a maior parte dos vídeos.
 */
export function ordenarGrupos<T extends { chave: string; semana: { views: number | null; media?: number | null }; mes: { views: number | null; media: number | null } }>(
  lista: T[],
  ordem: string,
): T[] {
  const valor = (g: T): number | null => {
    if (ordem === 'semana') return g.semana?.views ?? null
    if (ordem === 'por_video') return g.mes?.media ?? null
    return g.mes?.views ?? null
  }
  const nenhum = (g: T) => (g.chave === 'nenhum' ? 1 : 0)
  if (ordem === 'servidor' || !ordem) {
    return [...(lista || [])].sort((a, b) => nenhum(a) - nenhum(b))
  }
  return [...(lista || [])].sort((a, b) => {
    const n = nenhum(a) - nenhum(b)
    if (n) return n
    const va = valor(a)
    const vb = valor(b)
    if (va === null && vb === null) return 0
    if (va === null) return 1
    if (vb === null) return -1
    return vb - va
  })
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
 * Uma célula "rede × vídeo" da lista de vídeos: as views somadas dos posts
 * do vídeo nesta rede (a última leitura de cada um). `posts` vem do mais novo
 * pro mais velho. Sem número, a célula diz POR QUÊ — "—" sozinho é o que
 * fazia o Eduardo perguntar "será que demora?".
 */
export function celula(
  posts: PostagemDesempenho[] | undefined | null,
  agora: string | number | Date = new Date(),
  apagado = false,
): Celula {
  const vazio = { detalhe: '', dica: '', aviso: '' }
  const ps = (posts || []).filter(Boolean)
  // Saiu e foi apagado depois: "não postado" seria mentira (24/09/2026).
  if (!ps.length && apagado) return { ...vazio, estado: 'apagado', texto: 'apagado da rede', dica: 'O vídeo saiu nesta rede e foi apagado depois — não conta mais.' }
  if (!ps.length) return { ...vazio, estado: 'nao_postado', texto: 'não postado' }
  // Falhou HOJE mas já tinha lido antes: mostra a leitura boa e avisa.
  const falhaHoje = ps.find((p) => p.estado === 'falhou' && p.lido_em)
  const aviso = falhaHoje ? `a leitura de hoje falhou — mostrando a de ${ddmm(falhaHoje.lido_em)}` : ''
  const comViews = ps.filter((p) => (p.estado === 'ok' || p.estado === 'falhou')
    && p.acumulado && p.acumulado.views !== null && p.acumulado.views !== undefined)
  if (comViews.length) {
    const soma = comViews.reduce((a, p) => a + (p.acumulado.views as number), 0)
    let dica = ''
    if (ps.length > 1) {
      dica = `soma de ${qtd(comViews.length, 'postagem', 'postagens')}`
      if (ps.length > comViews.length) dica += ` (${ps.length - comViews.length} ainda sem número)`
    }
    return { ...vazio, aviso, estado: 'ok', texto: soma.toLocaleString('pt-BR'), dica }
  }
  const p = ps[0]
  if (p.estado === 'aguardando' || (p.estado === 'ok' && !p.lido_em)) {
    // Aguardar é o normal da primeira hora — cinza, nunca âmbar.
    // Post de outro dia ainda sem leitura (robô parado?) diz a data também —
    // "publicado às 12:04" de três dias atrás pareceria de hoje.
    let dica = ''
    if (p.publicado_em) {
      dica = diasEntre(partesBRT(p.publicado_em).dia, partesBRT(agora).dia) === 0
        ? `Publicado às ${hhmm(p.publicado_em)}. `
        : `Publicado em ${ddmm(p.publicado_em)} às ${hhmm(p.publicado_em)}. `
    }
    dica += 'A primeira leitura sai em até 1 hora.'
    return { ...vazio, estado: 'aguardando', texto: 'aguardando 1ª leitura', dica }
  }
  // Falhou sem NENHUMA leitura boa antes: não há número pra mostrar.
  if (p.estado === 'falhou' && !p.lido_em) {
    return { ...vazio, estado: 'falhou', texto: 'não consegui ler', dica: p.erro || '' }
  }
  // Leu, mas a rede não deu views (o Instagram sem insights): as curtidas dizem algo.
  return {
    ...vazio, aviso, estado: 'sem_views', texto: 'sem views',
    detalhe: `${num(p.acumulado || {}, 'curtidas')} curtidas · a conta não liberou insights`,
  }
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
 * incluída), com marcas tracejadas em 1, 3 e 7 dias de vida.
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
