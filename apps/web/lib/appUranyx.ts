// App Uranyx (06/10/2026): o módulo do DaVinci que cuida do app dos clientes
// Uranyx — catálogo (cópia do site conferida contra o DaVinci), manuais,
// receitas, apps recomendados, contas, "Não fui eu" e a fila do SAC.
//
// As telas chamam /api/app-uranyx/* (a API do DaVinci repassa para /admin/*
// da API do app, mesmos caminhos, com o token da equipe guardado no servidor:
// o navegador nunca vê o token). Contrato em
// app-uranyx/docs/integracao/conteudo-e-catalogo-v1.md, seção 6.
//
// Aqui ficam só os tipos e as funções puras (testadas em
// tests/app-uranyx-catalogo-sfc.cjs); a chamada em si mora em
// composables/useAppUranyx.ts.
import type { RouteTab } from '~/lib/navGroups'

// ─── Abas do módulo ─────────────────────────────────────────────────────────
// Todas só para admin; o resto da trava (APP_URANYX_USUARIOS) é o middleware
// `app-uranyx` e a própria API.
export const TABS_APP_URANYX: RouteTab[] = [
  { to: '/app-uranyx/catalogo', label: 'Catálogo', adminOnly: true },
  { to: '/app-uranyx/excecoes', label: 'Exceções', adminOnly: true },
  { to: '/app-uranyx/receitas', label: 'Receitas', adminOnly: true },
  { to: '/app-uranyx/apps', label: 'Apps recomendados', adminOnly: true },
  { to: '/app-uranyx/contas', label: 'Contas', adminOnly: true },
  { to: '/app-uranyx/fila', label: 'Fila do SAC', adminOnly: true },
]

// ─── Tipos (os JSON de /admin/* da API do app) ──────────────────────────────
export type Categoria = 'celular' | 'acessorio' | 'eletrodomestico'
export type OrigemSku = 'site' | 'manual'

export type SkuCatalogo = {
  codigo_base: string
  cor: string | null
  foto_url: string | null
  origem?: OrigemSku
}

export type ProdutoCatalogo = {
  id: string
  nome: string
  categoria: Categoria
  foto_url: string | null
  meses_hardware: number
  meses_software_extra: number
  tem_voltagem: boolean
  logo_uranyx_desde: string | null
  ativo: boolean
  skus: SkuCatalogo[]
  site_produto_id?: number | null
  site_slug?: string | null
  fora_do_site?: boolean
  sincronizado_em?: string | null
  foto_travada?: boolean
  criado_em?: string | null
  atualizado_em?: string | null
}

export type Conflito = { codigo_base: string; produto_site: string; ligado_a: string }

export type RelatorioSync = {
  quando: string | null
  ok: boolean
  erro: string | null
  produtos_site: number
  criados: number
  atualizados: number
  fora_do_site: number
  skus_ligados: number
  conflitos: Conflito[]
  linhas_desconhecidas: string[]
  skus_site_sem_davinci: string[]
  // `false` quando a conferência no DaVinci não rodou nesta cópia (a API do
  // app manda `skus_site_sem_davinci: null`): a lista vazia acima NÃO quer
  // dizer que está tudo certo.
  conferiu_davinci: boolean
  // Produtos do site com formato inválido, que ficaram de fora da cópia
  // (o id do site, ou "#posição" quando nem o id deu para ler).
  produtos_invalidos: string[]
  // Avisos da cópia, na frase da API do app.
  avisos: string[]
}

// Como a API do app manda (servicos/catalogo_copia.py, `Relatorio.json`).
export type RelatorioSyncApi = Partial<Omit<RelatorioSync, 'skus_site_sem_davinci' | 'conferiu_davinci'>> & {
  skus_site_sem_davinci?: string[] | null
  ligada?: boolean
}

// O relatório pode chegar incompleto (cópia que falhou no começo, ou nunca
// rodou: `null`). A tela sempre recebe as listas, mesmo vazias.
export function normalizarRelatorio(r: RelatorioSyncApi | null | undefined): RelatorioSync | null {
  if (!r || typeof r !== 'object') return null
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
  const l = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
  return {
    quando: r.quando ?? null,
    ok: r.ok === true,
    erro: r.erro ?? null,
    produtos_site: n(r.produtos_site),
    criados: n(r.criados),
    atualizados: n(r.atualizados),
    fora_do_site: n(r.fora_do_site),
    skus_ligados: n(r.skus_ligados),
    conflitos: l<Conflito>(r.conflitos),
    linhas_desconhecidas: l<string>(r.linhas_desconhecidas),
    skus_site_sem_davinci: l<string>(r.skus_site_sem_davinci),
    // Só o `null` explícito é "não rodou"; campo ausente (relatório antigo ou
    // cópia que falhou antes) fica como estava.
    conferiu_davinci: r.skus_site_sem_davinci !== null,
    produtos_invalidos: l<unknown>(r.produtos_invalidos).map(String),
    avisos: l<unknown>(r.avisos).filter((a): a is string => typeof a === 'string' && !!a.trim()),
  }
}

// O que precisa de alguém no relatório: cada conflito, linha desconhecida,
// SKU sem DaVinci e produto inválido; a conferência no DaVinci que não rodou
// conta 1. Os avisos que a API do app já manda para essas duas últimas
// ("formato inválido", "conferência indisponível") não contam de novo: só
// aviso a mais (um que a tela ainda não conhece) soma.
export function pendenciasRelatorio(r: RelatorioSync | null | undefined): number {
  if (!r) return 0
  const semConferencia = r.conferiu_davinci ? 0 : 1
  const avisosConhecidos = (r.produtos_invalidos.length ? 1 : 0) + semConferencia
  return r.conflitos.length + r.linhas_desconhecidas.length + r.skus_site_sem_davinci.length
    + r.produtos_invalidos.length + semConferencia + Math.max(0, r.avisos.length - avisosConhecidos)
}

// Selo do relatório. Sem `quando` é o relatório vazio que a API do app manda
// quando a cópia nunca rodou (o `erro` diz por quê: ainda não rodou ou está
// desligada): não é falha.
export type SeloRelatorio = 'ok' | 'falhou' | 'nunca_rodou'

export function seloRelatorio(r: Pick<RelatorioSync, 'quando' | 'ok'> | null | undefined): SeloRelatorio {
  if (!r || !r.quando) return 'nunca_rodou'
  return r.ok ? 'ok' : 'falhou'
}

export type SkuSemMapa = {
  codigo_base: string
  quantidade: number
  ultima_venda: string | null
  sku_exemplo: string | null
  descricao_exemplo: string | null
}

export type SkusSemMapa = { dias: number; desde: string; truncado: boolean; itens: SkuSemMapa[] }
export type SkuIgnorado = { codigo_base: string; motivo: string | null; criado_em?: string | null }

export type ArquivoEnviado = { id: string; url: string; mime: string; tamanho: number; nome: string }

export type Manual = {
  id: string
  titulo: string
  arquivo_id?: string
  url?: string | null
  tamanho?: number | null
  ordem: number
  ativo: boolean
  criado_em?: string | null
  atualizado_em?: string | null
}

export type Ajuste = { temperatura_c?: number; tempo_min?: number; funcao?: string }
export type Dificuldade = 'facil' | 'media' | 'dificil'

export type Receita = {
  id: string
  titulo: string
  foto_url: string | null
  youtube_url: string | null
  tempo_min: number
  rendimento: string
  dificuldade: Dificuldade
  ingredientes: string[]
  passos: string[]
  ajuste: Ajuste | null
  tags: string[]
  publicada: boolean
  eletrodomesticos: { id: string; nome: string }[]
  criado_em?: string | null
  atualizado_em?: string | null
}

export type AppRecomendado = {
  id: string
  categoria_id: string
  nome: string
  descricao: string
  icone_url: string
  pacote_android: string
  link_app_store: string | null
  gratis: boolean
  ordem: number
  ativo: boolean
}

export type CategoriaApps = { id: string; nome: string; ordem: number; ativo: boolean; apps: AppRecomendado[] }

export type ChamadoConta = {
  id: string
  protocolo: string | null
  produto_nome: string | null
  categoria: string | null
  estado: string
  enviado_em: string | null
  criado_em: string | null
}

export type Conta = {
  id: string
  nome: string | null
  documento_mascarado: string
  documento_tipo?: string | null
  email: string | null
  celular: string | null
  estado: string
  conta_teste?: boolean
  revisar_integridade?: boolean
  novidades?: boolean
  criado_em: string | null
  email_verificado_em?: string | null
  compra_confirmada_em?: string | null
  excluido_em?: string | null
  sessoes_ativas?: number
  reclamacoes_abertas?: number
  chamados?: { total: number; por_estado: Record<string, number>; ultimos: ChamadoConta[] }
}

export type Acesso = {
  evento: string
  criado_em: string | null
  plataforma: string | null
  app_versao: string | null
  instalacao_id: string | null
  ip: string | null
}

export type ContaResumida = Pick<Conta, 'id' | 'nome' | 'documento_mascarado' | 'email' | 'celular' | 'estado' | 'criado_em'>

export type Reclamacao = {
  id: string
  estado: 'aberta' | 'aceita' | 'recusada'
  prazo: string | null
  prazo_vencido: boolean
  criado_em: string | null
  decidido_em: string | null
  decisao_nota: string | null
  conta_atual: ContaResumida | null
  requerente: Record<string, unknown>
  emails?: Record<string, string>
}

export type ItemFila = {
  id: string
  estado: 'na_fila' | 'enviando' | 'falhou'
  protocolo: string | null
  produto_nome: string | null
  categoria: string | null
  pedido_bling: string | null
  usuario_id: string | null
  conta_teste: boolean
  tentativas: number
  proxima_tentativa_em: string | null
  ultimo_erro: string | null
  enviado_em: string | null
  criado_em: string | null
  atualizado_em: string | null
  atrasado_24h: boolean
}

// ─── Rótulos ────────────────────────────────────────────────────────────────
export const CATEGORIA_LABEL: Record<Categoria, string> = {
  celular: 'Celular',
  acessorio: 'Acessório',
  eletrodomestico: 'Eletrodoméstico',
}

export const DIFICULDADE_LABEL: Record<Dificuldade, string> = {
  facil: 'Fácil',
  media: 'Média',
  dificil: 'Difícil',
}

export const ESTADO_CONTA_LABEL: Record<string, string> = {
  ativa: 'Ativa',
  congelada: 'Congelada',
  excluida: 'Excluída',
}

export const ESTADO_FILA_LABEL: Record<string, string> = {
  na_fila: 'Na fila',
  enviando: 'Enviando',
  falhou: 'Falhou',
}

export const ESTADO_CHAMADO_LABEL: Record<string, string> = {
  rascunho: 'Rascunho',
  na_fila: 'Na fila',
  enviando: 'Enviando',
  enviado: 'Enviado',
  falhou: 'Falhou',
  descartado: 'Descartado',
}

export const ESTADO_RECLAMACAO_LABEL: Record<string, string> = {
  aberta: 'Aberta',
  aceita: 'Aceita',
  recusada: 'Recusada',
}

// O que aconteceu com cada aviso por e-mail depois da decisão do "Não fui eu".
export const AVISO_EMAIL_LABEL: Record<string, string> = {
  enviado: 'e-mail enviado',
  falhou: 'o e-mail não saiu',
  sem_email: 'sem e-mail cadastrado',
  sem_aviso: 'nada a avisar',
}

export const EVENTO_ACESSO_LABEL: Record<string, string> = {
  login: 'Entrou no app',
  cadastro: 'Criou a conta',
  senha_redefinida: 'Redefiniu a senha',
  senha_trocada: 'Trocou a senha',
  email_trocado: 'Trocou o e-mail',
  email_desfeito: 'Desfez a troca de e-mail',
  conta_excluida: 'Conta excluída',
  reivindicacao: 'Pedido de "Não fui eu"',
  congelada_painel: 'Congelada pela equipe',
  descongelada_painel: 'Descongelada pela equipe',
  descongelada_reclamacao: 'Descongelada (reclamação recusada)',
}

export function rotulo(mapa: Record<string, string>, valor: string | null | undefined): string {
  if (!valor) return '—'
  return mapa[valor] ?? valor.replace(/_/g, ' ')
}

// ─── "Não fui eu": quem pediu ───────────────────────────────────────────────
// O requerente que a API do app guarda (servicos/cadastro.py, `reivindicar`)
// e decifra para o painel (servicos/admin.py, `requerente`): email, celular,
// pedidos_provados (lista), instalacao_id, plataforma, integridade, em (ISO)
// e outros_casos_abertos. É o que a equipe usa para aceitar o pedido (e
// aceitar EXCLUI a conta atual): nada pode sumir da tela. Campo que a tela
// não conhece aparece com o nome cru; lista vira lista.
const REQUERENTE_CAMPOS: [string, string][] = [
  ['email', 'E-mail'],
  ['celular', 'Celular'],
  ['pedidos_provados', 'Pedidos comprovados'],
  ['em', 'Pediu em'],
  ['plataforma', 'Aparelho'],
  ['integridade', 'Integridade do aparelho'],
  ['outros_casos_abertos', 'Outros pedidos para este documento'],
  ['instalacao_id', 'Instalação do app'],
  ['documento', 'CPF/CNPJ'],
  ['cpf', 'CPF'],
  ['cnpj', 'CNPJ'],
  // Dado que não era JSON de objeto (servicos/admin.py guarda assim).
  ['texto', 'Dados'],
  ['valor', 'Dados'],
]

export const PLATAFORMA_LABEL: Record<string, string> = {
  android: 'Android',
  ios: 'iPhone (iOS)',
}

// Níveis do Play Integrity (servicos/integridade.py): o pior dos passos do cadastro.
export const INTEGRIDADE_LABEL: Record<string, string> = {
  ok: 'ok',
  basico: 'só a básica',
  indisponivel: 'não conferida',
  reprovado: 'reprovada',
}

export type CampoRequerente = { chave: string; label: string; valor: string | string[]; alerta: boolean }

function textoDoValor(v: unknown): string {
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'sim' : 'não'
  if (typeof v === 'number') return String(v)
  return JSON.stringify(v)
}

export function camposRequerente(d: Record<string, unknown> | null | undefined): CampoRequerente[] {
  if (!d || typeof d !== 'object' || d.erro === 'nao_decifrado') return []
  const conhecidos = REQUERENTE_CAMPOS.map(([k]) => k)
  const chaves = [
    ...conhecidos.filter((k) => k in d),
    ...Object.keys(d).filter((k) => !conhecidos.includes(k)),
  ]
  const out: CampoRequerente[] = []
  for (const chave of chaves) {
    const v = d[chave]
    if (v === null || v === undefined || v === '') continue
    const label = REQUERENTE_CAMPOS.find(([k]) => k === chave)?.[1] ?? chave.replace(/_/g, ' ')
    let valor: string | string[]
    let alerta = false
    if (Array.isArray(v)) valor = v.filter((x) => x !== null && x !== undefined && x !== '').map(textoDoValor)
    else if (chave === 'em' && typeof v === 'string') valor = dataHora(v)
    else if (chave === 'plataforma' && typeof v === 'string') valor = rotulo(PLATAFORMA_LABEL, v)
    else if (chave === 'integridade' && typeof v === 'string') {
      valor = rotulo(INTEGRIDADE_LABEL, v)
      alerta = v === 'basico' || v === 'reprovado'
    } else if (chave === 'outros_casos_abertos' && typeof v === 'number') {
      valor = v > 0 ? `${v} ${v === 1 ? 'aberto' : 'abertos'} quando pediu` : 'nenhum'
      alerta = v > 0
    } else valor = textoDoValor(v)
    out.push({ chave, label, valor, alerta })
  }
  return out
}

// ─── Erros ──────────────────────────────────────────────────────────────────
// A API do DaVinci devolve o status e o JSON de erro da API do app
// (`{erro, mensagem, campos}`); as travas dela mesma podem vir no formato do
// DaVinci (`{detail: {code}}`). Os dois viram a mesma frase.
export type ErroAppUranyx = {
  codigo: string
  texto: string
  campos: Record<string, string>
  status: number | null
  indisponivel: boolean
  // Sem resposta a tempo (no navegador ou entre o DaVinci e a API do app): numa
  // alteração, ela pode ter sido feita. Nunca dizer "nada foi alterado".
  demorou: boolean
}

export const TEXTO_INDISPONIVEL = 'API do app fora do ar ou não configurada'

// O 503 app_uranyx_indisponivel por tempo esgotado: a frase é a MSG_DEMOROU de
// apps/api/app/services/app_uranyx/repasse.py ("… não respondeu a tempo …").
export function semRespostaATempo(mensagem: string | null | undefined): boolean {
  return /n[ãa]o respondeu a tempo|demorou demais/i.test(mensagem || '')
}

const ERROS_CONHECIDOS: Record<string, string> = {
  app_uranyx_indisponivel: TEXTO_INDISPONIVEL,
  app_uranyx_restrito: 'Só quem está liberado para o App Uranyx pode usar esta tela.',
  arquivo_invalido: 'Arquivo não aceito: use JPG, PNG ou WebP até 5 MB, ou PDF até 25 MB.',
  em_uso: 'Este arquivo ainda está em uso e não pode ser apagado.',
}

const CAMPO_LABEL: Record<string, string> = {
  titulo: 'Título',
  nome: 'Nome',
  foto_url: 'Foto',
  foto_arquivo_id: 'Foto',
  icone_url: 'Ícone',
  icone_arquivo_id: 'Ícone',
  youtube_url: 'Link do YouTube',
  tempo_min: 'Tempo',
  rendimento: 'Rendimento',
  dificuldade: 'Dificuldade',
  ingredientes: 'Ingredientes',
  passos: 'Modo de preparo',
  'ajuste.temperatura_c': 'Ajuste: temperatura',
  'ajuste.tempo_min': 'Ajuste: tempo',
  'ajuste.funcao': 'Ajuste: função',
  tags: 'Tags',
  eletrodomesticos: 'Eletrodomésticos',
  meses_hardware: 'Garantia de hardware',
  meses_software_extra: 'Garantia extra de software',
  logo_uranyx_desde: 'Logo Uranyx desde',
  pacote_android: 'Pacote Android',
  link_app_store: 'Link da App Store',
  descricao: 'Descrição',
  ordem: 'Ordem',
  motivo: 'Motivo',
  codigo_base: 'Código-base',
  documento: 'CPF/CNPJ',
  arquivo: 'Arquivo',
  arquivo_id: 'Arquivo',
  nota: 'Nota',
}

export function campoLabel(campo: string): string {
  if (CAMPO_LABEL[campo]) return CAMPO_LABEL[campo]
  // `ingredientes.3` → "Ingredientes (item 4)"
  const m = campo.match(/^(.+)\.(\d+)$/)
  if (m && CAMPO_LABEL[m[1]]) return `${CAMPO_LABEL[m[1]]} (item ${Number(m[2]) + 1})`
  return campo
}

function statusDe(e: any): number | null {
  const st = e?.status ?? e?.statusCode ?? e?.response?.status
  return typeof st === 'number' && st > 0 ? st : null
}

function textoOuVazio(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

// Pedido sem resposta porque o tempo acabou (o `$fetch` cancela com
// TimeoutError; a mensagem crua fala em "timeout"/"timed out").
function tempoEsgotado(e: any): boolean {
  return e?.name === 'TimeoutError' || e?.cause?.name === 'TimeoutError' || /time(d)? ?out/i.test(String(e?.message ?? ''))
}

// O método do pedido que falhou: o FetchError do `$fetch` traz as `options`
// e começa a mensagem com "[POST] …".
function metodoDe(e: any): string {
  const m = e?.options?.method ?? (String(e?.message ?? '').match(/^\[([A-Za-z]+)\]/) || [])[1]
  return typeof m === 'string' && m ? m.toUpperCase() : 'GET'
}

export function erroAppUranyx(e: any, padrao = 'Não deu certo'): ErroAppUranyx {
  const status = statusDe(e)
  const data = e?.data
  const detail = data && typeof data === 'object' ? data.detail : undefined
  // O corpo do erro: o da API do app (`{erro, mensagem}`) ou o do DaVinci (`{detail: {...}}`).
  const corpo: any = detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : data
  const campos: Record<string, string> = {}

  let codigo = ''
  let mensagem = ''
  if (corpo && typeof corpo === 'object') {
    codigo = textoOuVazio(corpo.erro) || textoOuVazio(corpo.code)
    // `message` só vale dentro do `detail` do DaVinci. Solto no corpo é o erro
    // do proxy do Nuxt sem a API atrás ("Bad Gateway", "Dev server is
    // unavailable."): texto técnico, cai na frase pelo status.
    mensagem = textoOuVazio(corpo.mensagem) || (corpo === detail ? textoOuVazio(corpo.message) : '')
    if (corpo.campos && typeof corpo.campos === 'object') {
      for (const [k, v] of Object.entries(corpo.campos)) campos[k] = String(v)
    }
  }
  if (typeof detail === 'string') codigo = codigo || detail
  // 422 do próprio DaVinci (Pydantic): [{loc, msg}].
  if (Array.isArray(detail)) {
    for (const x of detail) {
      const loc = Array.isArray(x?.loc) ? x.loc.filter((p: unknown) => p !== 'body' && p !== 'query' && p !== 'path') : []
      campos[loc.join('.') || 'corpo'] = String(x?.msg || 'Valor inválido').replace(/^Value error,\s*/i, '')
    }
    codigo = codigo || 'validacao'
  }

  const indisponivel = codigo === 'app_uranyx_indisponivel'
  const semResposta = !status && tempoEsgotado(e)
  const demorou = semResposta || (indisponivel && semRespostaATempo(mensagem))
  let texto: string
  // A frase da API do DaVinci diz o porquê (fora do ar, sem configuração, ou
  // "não respondeu a tempo… confira se ela foi feita antes de repetir").
  if (indisponivel) texto = mensagem || TEXTO_INDISPONIVEL
  else if (ERROS_CONHECIDOS[codigo] && !mensagem) texto = ERROS_CONHECIDOS[codigo]
  else if (mensagem) texto = mensagem
  else if (status === 404 && (codigo === 'Not Found' || !codigo)) {
    // O 404 seco do FastAPI: a rota não existe na API do DaVinci que está no ar.
    texto = 'A API do DaVinci ainda não tem esta parte do App Uranyx (falta subir a versão nova).'
  } else if (status === 401) texto = 'Sua sessão do DaVinci terminou. Entre de novo.'
  else if (status === 403) texto = 'Sem permissão para isso.'
  // Sem status: o pedido nem teve resposta (sem internet, fetch que falhou,
  // tempo esgotado). Nunca o texto cru ("Failed to fetch", "FetchError: …").
  // Numa alteração sem resposta, ela pode ter sido feita: conferir antes de repetir.
  else if (semResposta && metodoDe(e) !== 'GET') {
    texto = `${padrao}: o servidor demorou demais para responder e a alteração pode ter sido feita. Confira antes de repetir.`
  } else if (semResposta) texto = `${padrao}: o servidor demorou demais para responder. Tente de novo.`
  else if (!status) texto = `${padrao}: sem conexão com o servidor. Confira a internet e tente de novo.`
  else if (status >= 500) texto = `${padrao}: o servidor respondeu com erro (${status}). Tente de novo em instantes.`
  else texto = padrao
  return { codigo, texto, campos, status, indisponivel, demorou }
}

// Uma linha por campo com problema ("Título: Muito curto"), para toast e caixas de erro.
export function linhasDoErro(er: Pick<ErroAppUranyx, 'campos'>): string[] {
  return Object.entries(er.campos).map(([k, v]) => `${campoLabel(k)}: ${v}`)
}

// ─── Caminhos ───────────────────────────────────────────────────────────────
// O código-base vai no caminho codificado: pode ter espaço, acento ou barra
// ("kit promo 01", "dg300/azul"), como veio do Bling.
export function codigoNoCaminho(codigo: string): string {
  return encodeURIComponent(codigo)
}

// ─── Arquivos ───────────────────────────────────────────────────────────────
// Os mesmos limites da API do app (ela decide o tipo pelos bytes; aqui é só
// para não subir 30 MB e receber um 422).
export const FOTO_MAX_BYTES = 5 * 1024 * 1024
export const PDF_MAX_BYTES = 25 * 1024 * 1024
export const FOTO_TIPOS = ['image/jpeg', 'image/png', 'image/webp']
export const FOTO_LADO_MAX = 1600
export const FOTO_QUALIDADE = 0.8

export function problemaFoto(f: { size: number; type: string }, comprimir = false): string | null {
  if (f.type && !FOTO_TIPOS.includes(f.type)) return 'Use uma foto JPG, PNG ou WebP.'
  // Foto que vai ser comprimida aqui pode chegar grande (a do celular tem 8 MB).
  if (!comprimir && f.size > FOTO_MAX_BYTES) return 'A foto passa de 5 MB. Escolha uma menor.'
  return null
}

export function problemaPdf(f: { size: number; type: string; name: string }): string | null {
  const ehPdf = f.type === 'application/pdf' || (!f.type && /\.pdf$/i.test(f.name))
  if (!ehPdf) return 'O manual precisa ser um PDF.'
  if (f.size > PDF_MAX_BYTES) return 'O PDF passa de 25 MB. Reduza o arquivo e tente de novo.'
  return null
}

// Tamanho final da foto: o lado maior vira no máximo `max`, sem aumentar.
export function dimensoesReduzidas(largura: number, altura: number, max = FOTO_LADO_MAX): { largura: number; altura: number } {
  if (!(largura > 0) || !(altura > 0)) return { largura: 0, altura: 0 }
  const fator = Math.min(1, max / Math.max(largura, altura))
  return {
    largura: Math.max(1, Math.round(largura * fator)),
    altura: Math.max(1, Math.round(altura * fator)),
  }
}

export function nomeJpg(nome: string): string {
  const base = (nome || 'foto').replace(/\.[^./\\]+$/, '').trim() || 'foto'
  return `${base}.jpg`
}

// Título do manual a partir do nome do arquivo ("Manual A17 Pro.pdf" → "Manual A17 Pro").
export function tituloDoArquivo(nome: string): string {
  const t = (nome || '').replace(/\.pdf$/i, '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim()
  return (t || 'Manual').slice(0, 80)
}

export function formatarTamanho(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MB`
}

// Foto do celular para JPEG de no máximo 1600 px e qualidade 0,8 (só no
// navegador). O fundo branco evita que o PNG transparente vire preto.
export async function comprimirFoto(arquivo: File, max = FOTO_LADO_MAX, qualidade = FOTO_QUALIDADE): Promise<File> {
  const url = URL.createObjectURL(arquivo)
  try {
    const img = await new Promise<HTMLImageElement>((ok, falha) => {
      const i = new Image()
      i.onload = () => ok(i)
      i.onerror = () => falha(new Error('Não deu para abrir esta foto.'))
      i.src = url
    })
    const { largura, altura } = dimensoesReduzidas(img.naturalWidth, img.naturalHeight, max)
    if (!largura || !altura) throw new Error('Não deu para abrir esta foto.')
    const canvas = document.createElement('canvas')
    canvas.width = largura
    canvas.height = altura
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('O navegador não conseguiu reduzir a foto.')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, largura, altura)
    ctx.drawImage(img, 0, 0, largura, altura)
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, 'image/jpeg', qualidade))
    if (!blob) throw new Error('O navegador não conseguiu reduzir a foto.')
    return new File([blob], nomeJpg(arquivo.name), { type: 'image/jpeg' })
  } finally {
    URL.revokeObjectURL(url)
  }
}

// ─── Formulários ────────────────────────────────────────────────────────────
// Lista "um por linha" (ingredientes, passos) → itens sem vazios.
export function linhasParaLista(texto: string): string[] {
  return (texto || '').split('\n').map((s) => s.trim()).filter(Boolean)
}

export function listaParaLinhas(lista: string[] | null | undefined): string {
  return (lista ?? []).join('\n')
}

// Tags separadas por vírgula → minúsculas, sem repetir (a API faz o mesmo).
export function tagsDoTexto(texto: string): string[] {
  const out: string[] = []
  for (const t of (texto || '').split(',')) {
    const v = t.trim().toLowerCase()
    if (v && !out.includes(v)) out.push(v)
  }
  return out
}

const HOSTS_YOUTUBE = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be']

export function youtubeValido(url: string): boolean {
  try {
    const u = new URL((url || '').trim())
    return u.protocol === 'https:' && HOSTS_YOUTUBE.includes(u.hostname.toLowerCase())
  } catch {
    return false
  }
}

export function httpsValido(url: string): boolean {
  try {
    const u = new URL((url || '').trim())
    return u.protocol === 'https:' && !!u.hostname
  } catch {
    return false
  }
}

// Mesmo padrão da API (`_PACOTE` em rotas/admin.py): com.empresa.app
export const PACOTE_ANDROID = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/

export function linkPlayStore(pacote: string): string {
  return `https://play.google.com/store/apps/details?id=${encodeURIComponent(pacote)}`
}

// Inteiro dentro da faixa, ou null (campo vazio ou fora da faixa).
export function inteiroOuNull(v: unknown, min: number, max: number): number | null {
  if (v === '' || v == null) return null
  const n = typeof v === 'number' ? v : Number(String(v).trim().replace(',', '.'))
  if (!Number.isInteger(n) || n < min || n > max) return null
  return n
}

// CPF/CNPJ como a API quer: sem pontuação, letras maiúsculas (CNPJ alfanumérico).
export function documentoLimpo(v: string): string {
  return (v || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase()
}

// Só os campos que mudaram (PATCH): campo novo da API que ainda não subiu não
// vai junto à toa (a API recusa campo que não conhece).
export function diferencas<T extends Record<string, unknown>>(antes: Partial<T>, depois: T): Partial<T> {
  const out: Partial<T> = {}
  for (const k of Object.keys(depois) as (keyof T)[]) {
    const a = antes[k] ?? null
    const d = depois[k] ?? null
    if (JSON.stringify(a) !== JSON.stringify(d)) out[k] = depois[k]
  }
  return out
}

// ─── Datas ──────────────────────────────────────────────────────────────────
const BRT = 'America/Sao_Paulo'

export function dataHora(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', { timeZone: BRT, day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// `AAAA-MM-DD` → `DD/MM/AAAA` sem passar por fuso (é data, não hora).
export function dataCurta(v: string | null | undefined): string {
  if (!v) return '—'
  const m = v.match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${m[3]}/${m[2]}/${m[1]}` : v
}

export function garantiaTexto(p: Pick<ProdutoCatalogo, 'meses_hardware' | 'meses_software_extra'>): string {
  const hw = `${p.meses_hardware} ${p.meses_hardware === 1 ? 'mês' : 'meses'}`
  return p.meses_software_extra > 0 ? `${hw} + ${p.meses_software_extra} de software` : hw
}
