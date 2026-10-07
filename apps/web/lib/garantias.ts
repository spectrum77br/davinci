// Pós-venda › Garantias (07/10/2026): Painel de Garantia Uranyx.
// Documento "Painel de Garantia — Uranyx (DaVinci)". As REGRAS moram no
// backend (apps/api/app/services/garantia.py, com os pontos do §8 decididos
// pelo dono num lugar só); aqui ficam os tipos do contrato
// (apps/api/app/schemas/garantia.py), os rótulos e as contas que a TELA faz
// só para mostrar antes de salvar (máscara do CPF, prévia da cobertura) — o
// que vale é sempre o que o servidor devolve.

// ─── contrato da API (/api/garantias) ───────────────────────────────────────
export type StatusGarantia = 'aguardando_entrega' | 'ativa' | 'somente_software' | 'expirada'
export type FiltroStatus = 'todos' | StatusGarantia | 'entregue_sem_data' | 'hw_vence_30d'
export type Cobertura = 'coberto' | 'fora_da_garantia' | 'sem_data_de_entrega'
export type TipoProblema = 'hardware' | 'software'

export type PessoaRef = { id: string | null; nome: string }

export type GarantiaLinha = {
  id: number
  cliente_nome: string
  cpf_mascarado: string | null
  nf_numero: string
  nf_serie: string
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  data_inicio: string | null
  fim_hardware: string | null
  fim_software: string | null
  status: StatusGarantia
  status_rotulo: string
  // "Aguardando entrega" de pedido que o Bling JÁ dá como entregue: falta a
  // data no DaVinci (a tela diz "Entregue — sem data no DaVinci").
  entregue_sem_data: boolean
  atendimentos: number
  criado_em: string
}

export type Indicadores = {
  ativas: number
  somente_software: number
  hw_vence_30d: number
  atendimentos_no_mes: number
  aguardando_entrega: number
  entregue_sem_data: number
  expiradas: number
  total: number
}

export type GarantiaLista = { itens: GarantiaLinha[]; total: number; indicadores: Indicadores; hoje: string }

export type Prazo = { fim: string; dias_total: number; dias_restantes: number; coberto_hoje: boolean }

export type Entrega = {
  data: string | null
  origem: string | null
  origem_rotulo: string | null
  em: string | null
  verificada_em?: string | null
}

export type ItemPedido = { descricao: string | null; sku: string | null; quantidade: number | null; uranyx: boolean }

export type AnexoGarantia = {
  mensagem_id: string | null
  tipo: string
  nome: string | null
  url_original: string | null
  baixado: boolean
  motivo: string | null
  arquivo_url: string | null
  content_type: string | null
  tamanho: number | null
}

export type MensagemCopiada = {
  id: string
  autor: string
  origem: string | null
  tipo: string | null
  texto: string | null
  enviada_em: string | null
  anexos: Record<string, unknown>[]
}

export type AtendimentoGarantia = {
  id: number
  garantia_id: number
  data_atendimento: string
  atendente: PessoaRef
  conversa_id: string
  conversa_link: string
  conversa_plataforma: string | null
  conversa_canal: string | null
  conversa_conta: string | null
  conversa_pedido: string | null
  resumo: string
  mensagens: MensagemCopiada[]
  anexos: AnexoGarantia[]
  tipo_problema: TipoProblema
  cobertura: Cobertura
  cobertura_rotulo: string
  fim_considerado: string | null
  cobertura_hoje: Cobertura
  cobertura_hoje_rotulo: string
  solucao: string
  criado_em: string
}

export type PermissoesGarantia = { cadastrar: boolean; registrar_atendimento: boolean; ver_cpf: boolean; ver_log: boolean }

export type GarantiaDetalhe = GarantiaLinha & {
  cpf: string | null
  cpf_completo: boolean
  nf_chave: string | null
  nf_emitente_cnpj: string | null
  itens: ItemPedido[]
  entrega: Entrega
  hardware: Prazo | null
  software: Prazo | null
  criado_por: PessoaRef | null
  atualizado_em: string | null
  atualizado_por: PessoaRef | null
  atendimentos_lista: AtendimentoGarantia[]
  permissoes: PermissoesGarantia
  avisos?: string[]
}

export type LogLinha = {
  id: number
  acao: string
  campo: string | null
  valor_anterior: string | null
  valor_novo: string | null
  detalhe: string | null
  pessoa: PessoaRef
  em: string
}

export type NotaDoPedido = {
  numero: string
  serie: string
  chave: string
  emitida_em: string | null
  valor: number | null
  papel: 'produto' | 'embalagem' | 'indefinida'
}

export type PedidoParaCadastro = {
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  data_pedido: string | null
  situacao: string | null
  itens: ItemPedido[]
  produto_uranyx: boolean
  entrega: Entrega
  prazos: { data_inicio: string | null; fim_hardware: string | null; fim_software: string | null; status: StatusGarantia; status_rotulo: string; entregue_sem_data: boolean }
  notas: NotaDoPedido[]
  nf_sugerida: NotaDoPedido | null
  nome_sugerido: string | null
  nome_origem: string | null
  cpf_sugerido: string | null
  cpf_sugerido_mascarado: string | null
  garantias_existentes: GarantiaLinha[]
  avisos: string[]
}

export type Vinculo = {
  atendimento_id: number
  garantia_id: number
  data_atendimento: string
  tipo_problema: TipoProblema
  cobertura: Cobertura
  cobertura_rotulo: string
}

// O bloco "Garantia" da conversa no /atendimento. Nunca traz o CPF.
export type SituacaoConversa = {
  conversa_id: string
  pedido_encontrado: boolean
  pedido_bling: string | null
  pedido_marketplace: string | null
  produto_uranyx: boolean
  cpf_conhecido: boolean
  alerta_cpf_sem_garantia: boolean
  garantias: GarantiaLinha[]
  busca_sugerida: string | null
  vinculos: Vinculo[]
}

export type Regras = {
  meses_hardware: number
  meses_software: number
  ultimo_dia_coberto: boolean
  cadastro_antes_da_entrega: boolean
  garantia_por: string
  vinculo_automatico: boolean
  recalcular_quando_entrega_mudar: boolean
  dias_alerta_hardware: number
  dias_pausa_atendimento: number
  status: Record<string, string>
  coberturas: Record<string, string>
  origens_entrega: Record<string, string>
}

// Os pontos do §8 como o dono decidiu em 07/10/2026 (iguais às constantes de
// services/garantia.py). A tela pede GET /regras; isto só vale se ele falhar.
export const REGRAS_PADRAO: Regras = {
  meses_hardware: 3,
  meses_software: 12,
  ultimo_dia_coberto: true,
  cadastro_antes_da_entrega: true,
  garantia_por: 'nf',
  vinculo_automatico: false,
  recalcular_quando_entrega_mudar: true,
  dias_alerta_hardware: 30,
  dias_pausa_atendimento: 7,
  status: {},
  coberturas: {},
  origens_entrega: {},
}

// ─── rótulos ────────────────────────────────────────────────────────────────
// As CLASSES de cor ficam nos componentes (GarantiaStatus, GarantiaCobertura,
// GarantiaPrazoBarra): o Tailwind só lê components/pages/layouts — classe que
// existe só aqui em lib/ não vira CSS (o tema escuro sumia).
// Status com o emoji do documento (§2): 🟡 🟢 🔵 🔴.
export const STATUS: Record<StatusGarantia, { rotulo: string; emoji: string; dica: string }> = {
  aguardando_entrega: {
    rotulo: 'Aguardando entrega',
    emoji: '🟡',
    dica: 'cadastrada, mas o pedido ainda não tem data de entrega — os prazos entram sozinhos quando ela aparecer',
  },
  ativa: {
    rotulo: 'Ativa',
    emoji: '🟢',
    dica: 'hoje está dentro do prazo de hardware (hardware e software cobertos)',
  },
  somente_software: {
    rotulo: 'Somente software',
    emoji: '🔵',
    dica: 'o prazo de hardware acabou; o de software continua',
  },
  expirada: {
    rotulo: 'Expirada',
    emoji: '🔴',
    dica: 'os dois prazos acabaram',
  },
}
// O "Aguardando entrega" de pedido que o Bling já dá como entregue (o
// DaVinci não tem a data: a Logística só guarda a data desde 15/07/2026).
// Mesmo status (sem data não há prazo — RN01), outro texto: não sugerir que
// o produto ainda não chegou.
export const ENTREGUE_SEM_DATA = {
  rotulo: 'Entregue — sem data no DaVinci',
  emoji: '🟡',
  dica: 'o Bling já marca o pedido como entregue, mas nenhuma fonte do DaVinci tem a data de entrega — sem ela não há prazo (a data inicial não se digita)',
}
export function statusInfo(s: string | null | undefined, entregueSemData = false) {
  if (s === 'aguardando_entrega' && entregueSemData) return ENTREGUE_SEM_DATA
  return STATUS[(s || '') as StatusGarantia] ?? { rotulo: s || '—', emoji: '⚪', dica: '' }
}

export const FILTROS_STATUS: { value: FiltroStatus; label: string }[] = [
  { value: 'todos', label: 'Todos' },
  { value: 'ativa', label: '🟢 Ativa' },
  { value: 'somente_software', label: '🔵 Somente software' },
  { value: 'expirada', label: '🔴 Expirada' },
  { value: 'aguardando_entrega', label: '🟡 Aguardando entrega' },
  { value: 'entregue_sem_data', label: '🟡 Entregue — sem data no DaVinci' },
  { value: 'hw_vence_30d', label: 'Hardware vence em 30 dias' },
]

export const COBERTURA: Record<Cobertura, { rotulo: string; emoji: string }> = {
  coberto: { rotulo: 'Coberto', emoji: '✅' },
  fora_da_garantia: { rotulo: 'Fora da garantia', emoji: '⛔' },
  sem_data_de_entrega: { rotulo: 'Sem data de entrega', emoji: '⏳' },
}
export function coberturaInfo(c: string | null | undefined) {
  return COBERTURA[(c || '') as Cobertura] ?? { rotulo: c || '—', emoji: '' }
}

export const TIPOS_PROBLEMA: { value: TipoProblema; label: string; dica: string }[] = [
  { value: 'hardware', label: 'Hardware', dica: 'defeito físico: tela, bateria, carregamento, botão, placa…' },
  { value: 'software', label: 'Software', dica: 'sistema, app, atualização, configuração…' },
]

// Avisos que não bloqueiam (GET /pedido e POST/PUT).
export const AVISOS: Record<string, string> = {
  pedido_sem_produto_uranyx: 'O pedido não tem produto Uranyx (SKU u…, dg… ou a0…). Confira se é o pedido certo.',
  aguardando_entrega: 'O pedido ainda não tem data de entrega: a garantia fica "Aguardando entrega" e os prazos entram sozinhos quando a entrega aparecer.',
  entregue_sem_data: 'O Bling já dá o pedido como entregue, mas o DaVinci não tem a data de entrega (a Logística só guarda a data desde 15/07/2026). A garantia fica sem prazos até a data aparecer — a data inicial não se digita.',
  pedido_ja_tem_garantia: 'Este pedido já tem garantia cadastrada (em outra NF) — confira se não é o mesmo aparelho.',
  cpf_diferente_do_pedido: 'O CPF informado é diferente do CPF que está no pedido.',
  nf_nao_e_do_pedido: 'A NF informada não está entre as notas deste pedido.',
  nf_de_embalagem: 'A NF informada parece ser a de embalagem (valor baixo) — a do produto é outra.',
}
export function textoAviso(cod: string): string {
  return AVISOS[cod] || cod
}

// Por que o anexo da conversa não foi guardado (AnexoOut.motivo).
export const MOTIVOS_ANEXO: Record<string, string> = {
  sem_link: 'só o nome do arquivo (o Mercado Livre não manda o link)',
  link_expirado: 'o link da plataforma já tinha expirado',
  maior_que_8mb: 'maior que 8 MB — ficou só o link',
  tipo_nao_suportado: 'tipo de arquivo não aceito (só foto, vídeo e PDF)',
  host_nao_permitido: 'link fora dos endereços das plataformas',
  limite_de_anexos: 'passou do limite (10 arquivos, 40 MB por atendimento)',
  erro_ao_baixar: 'não consegui baixar da plataforma',
}

export const ACOES_LOG: Record<string, string> = {
  cadastrou: 'Cadastrou',
  alterou: 'Alterou',
  consultou: 'Consultou',
  registrou_atendimento: 'Registrou atendimento',
  recalculou: 'Recalculou pela entrega',
  buscou_cpf: 'Buscou pelo CPF completo',
}
export const CAMPOS_LOG: Record<string, string> = {
  pedido: 'Pedido',
  nf_numero: 'NF',
  nf_serie: 'Série da NF',
  cpf: 'CPF',
  cliente_nome: 'Nome do cliente',
  data_inicio: 'Data inicial (entrega)',
  fim_hardware: 'Fim do hardware',
  fim_software: 'Fim do software',
  entrega_origem: 'Origem da entrega',
}

const NOMES_PLATAFORMA: Record<string, string> = {
  ml: 'Mercado Livre', mercadolivre: 'Mercado Livre', shopee: 'Shopee', tiktok: 'TikTok', amazon: 'Amazon',
  magalu: 'Magalu', temu: 'Temu', aliexpress: 'AliExpress', site: 'Site', instagram: 'Instagram',
}
export function nomePlataforma(p: string | null | undefined): string {
  if (!p) return ''
  return NOMES_PLATAFORMA[p.toLowerCase()] || p
}

// ─── erros ──────────────────────────────────────────────────────────────────
export const ERROS: Record<string, string> = {
  cpf_invalido: 'CPF inválido — confira os dígitos.',
  nf_invalida: 'NF inválida — use só números (até 9 dígitos).',
  nf_serie_invalida: 'Série inválida — até 3 números.',
  nome_invalido: 'Informe o nome do cliente (mínimo 3 letras).',
  pedido_nao_encontrado: 'Pedido não encontrado no DaVinci — confira o número (do Bling ou da plataforma).',
  pedido_sem_entrega: 'O pedido ainda não tem data de entrega — cadastre a garantia depois da entrega.',
  garantia_duplicada: 'Já existe garantia para este CPF nesta NF.',
  garantia_nao_encontrada: 'Garantia não encontrada (ou é de uma loja fora da sua equipe).',
  atendimento_restrito: 'Você não tem acesso ao Atendimento para copiar a conversa.',
  conversa_nao_encontrada: 'Conversa não encontrada — o Direct do Instagram não pode ser vinculado.',
  mensagem_nao_encontrada: 'Alguma mensagem escolhida não é desta conversa — atualize a conversa e escolha de novo.',
  atendimento_repetido: 'Este atendimento acabou de ser vinculado (clique duplo?) — não foi gravado de novo.',
  anexo_nao_encontrado: 'Anexo não encontrado.',
  muitas_buscas_por_cpf: 'Muitas buscas pelo CPF completo em pouco tempo — espere um pouco ou busque pelo pedido, pela NF ou pelo nome.',
  forbidden: 'Sem permissão para isso.',
}

const MSG_PYDANTIC: [RegExp, string][] = [
  [/extra (inputs|fields) (are )?not permitted/i, 'Este campo é calculado pelo sistema e não pode ser enviado.'],
  [/at least 1 character/i, 'Campo obrigatório.'],
  [/at least 3 characters/i, 'Mínimo de 3 caracteres.'],
  [/at least 11 characters/i, 'CPF incompleto.'],
  [/at most (\d+) characters/i, 'Texto longo demais.'],
  [/field required/i, 'Campo obrigatório.'],
  [/input should be 'hardware' or 'software'/i, 'Escolha Hardware ou Software.'],
]

/**
 * Erro da API → mensagem por campo (embaixo do campo) e uma geral. Os nossos
 * vêm em `detail: {code, campo?}`; o 422 do Pydantic em `detail: [{loc,msg}]`.
 * `garantiaId` / `atendimentoId` acompanham o 409 (para abrir a existente).
 */
export function errosDaApi(e: any): { campos: Record<string, string>; geral: string; garantiaId: number | null; atendimentoId: number | null } {
  const det = e?.data?.detail
  const campos: Record<string, string> = {}
  if (Array.isArray(det)) {
    for (const x of det) {
      const campo = Array.isArray(x?.loc) ? String(x.loc[x.loc.length - 1]) : ''
      const msg = String(x?.msg || '').replace(/^Value error,\s*/i, '')
      const pt = MSG_PYDANTIC.find(([re]) => re.test(msg))?.[1] || msg
      if (campo && campo !== 'body' && !campos[campo]) campos[campo] = pt
    }
    return { campos, geral: Object.keys(campos).length ? '' : 'Dados inválidos.', garantiaId: null, atendimentoId: null }
  }
  const garantiaId = typeof det?.garantia_id === 'number' ? det.garantia_id : null
  const atendimentoId = typeof det?.atendimento_id === 'number' ? det.atendimento_id : null
  const code = typeof det?.code === 'string' ? det.code : typeof det === 'string' ? det : ''
  const st = Number(e?.statusCode ?? e?.status ?? e?.response?.status ?? 0) || 0
  let msg = (code && ERROS[code]) || code
  // O 429 da busca pelo CPF completo vem com `retry_after` (segundos).
  if (code === 'muitas_buscas_por_cpf' && typeof det?.retry_after === 'number') {
    msg = `${msg} (de novo em ${Math.max(1, Math.ceil(det.retry_after / 60))} min)`
  }
  if (!msg) {
    if (st === 403) msg = ERROS.forbidden
    else if (st === 401) msg = 'Sua sessão expirou — entre de novo.'
    else if (!st) msg = 'Sem conexão com o servidor — confira a internet e tente de novo.'
    else if (st >= 500) msg = `O servidor não respondeu direito (erro ${st}) — tente de novo em instantes.`
    else msg = 'Não deu certo.'
  }
  if (det?.campo) {
    campos[String(det.campo)] = msg
    return { campos, geral: '', garantiaId, atendimentoId }
  }
  return { campos, geral: msg, garantiaId, atendimentoId }
}

// ─── permissões (§6: Cadastrar, Consultar, Registrar atendimento; e o CPF) ──
type UsuarioLike = {
  role?: string | null
  permissions?: Record<string, { view?: boolean; edit?: boolean; delete?: boolean } | undefined> | null
} | null | undefined

export type AcessoGarantia = {
  // Consultar: a página /garantias, o detalhe e os anexos.
  consulta: boolean
  // Cadastrar: nova garantia, corrigir o que foi informado, conferir a entrega.
  cadastra: boolean
  // Registrar atendimento: o "Vincular à garantia" no /atendimento.
  registra: boolean
  // CPF completo no detalhe (a lista é sempre mascarada).
  veCpf: boolean
  // O bloco "Garantia" da conversa (GET /conversa/{id}) e a busca do vínculo:
  // quem consulta OU registra (o `view` sozinho de "Registrar atendimento"
  // não conta — o backend também recusa).
  ve: boolean
  // Log de quem consultou/alterou: só admin.
  log: boolean
}

export function acessoGarantia(u: UsuarioLike): AcessoGarantia {
  if (!u) return { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false }
  const admin = u.role === 'admin'
  const p = (r: string, a: 'view' | 'edit') => admin || u.permissions?.[r]?.[a] === true
  const consulta = p('garantias', 'view') || p('garantias', 'edit')
  const registra = p('garantias_atendimento', 'edit')
  return {
    consulta,
    cadastra: p('garantias', 'edit'),
    registra,
    veCpf: p('garantias_cpf', 'view'),
    ve: consulta || registra,
    log: admin,
  }
}

// ─── CPF ────────────────────────────────────────────────────────────────────
export function soDigitos(v: unknown): string {
  return String(v ?? '').replace(/\D/g, '')
}

/** Máscara enquanto digita: "52998224725" → "529.982.247-25" (aceita colar formatado). */
export function formatarCpf(v: unknown): string {
  const d = soDigitos(v).slice(0, 11)
  if (d.length <= 3) return d
  if (d.length <= 6) return `${d.slice(0, 3)}.${d.slice(3)}`
  if (d.length <= 9) return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6)}`
  return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`
}

/** RN06 — o mesmo cálculo do backend (services/garantia.cpf_valido). */
export function cpfValido(v: unknown): boolean {
  if (v === null || v === undefined || /[^\d.\-\s]/.test(String(v))) return false
  const d = soDigitos(v)
  if (d.length !== 11 || new Set(d).size === 1) return false
  for (const n of [9, 10]) {
    let soma = 0
    for (let i = 0; i < n; i++) soma += Number(d[i]) * (n + 1 - i)
    const dv = ((soma * 10) % 11) % 10
    if (dv !== Number(d[n])) return false
  }
  return true
}

/** O que a lista mostra (§6): ***.456.789-** */
export function mascararCpf(v: unknown): string | null {
  const d = soDigitos(v)
  return d.length === 11 ? `***.${d.slice(3, 6)}.${d.slice(6, 9)}-**` : null
}

/** Mensagem do campo CPF (vazia = ok). */
export function erroDoCpf(v: string): string {
  const d = soDigitos(v)
  if (!d) return 'Informe o CPF.'
  if (d.length < 11) return 'CPF incompleto — são 11 dígitos.'
  return cpfValido(v) ? '' : 'CPF inválido — confira os dígitos.'
}

// ─── datas (sempre no fuso de São Paulo) ────────────────────────────────────
const _DIA_SP = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit' })

/** Instante → o dia em São Paulo (YYYY-MM-DD); data pura passa como está. */
export function diaEmSP(iso: string | null | undefined): string | null {
  if (!iso) return null
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : _DIA_SP.format(d)
}

/** "2026-10-07" → "07/10/2026"; instante → o dia em São Paulo. */
export function dataBR(iso: string | null | undefined): string {
  const dia = diaEmSP(iso)
  if (!dia) return '—'
  const [a, m, d] = dia.split('-')
  return `${d}/${m}/${a}`
}

/** "2026-10-07" → "07/10/26" (a tabela do §4.1). */
export function dataCurta(iso: string | null | undefined): string {
  const dia = diaEmSP(iso)
  if (!dia) return '—'
  const [a, m, d] = dia.split('-')
  return `${d}/${m}/${a.slice(2)}`
}

export function dataHoraBR(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function hojeSP(agora = Date.now()): string {
  return _DIA_SP.format(new Date(agora))
}

/** Ponto 1: com o último dia coberto, vale até `fim` inclusive. */
export function coberto(fim: string | null | undefined, dia: string, ultimoDiaCoberto = true): boolean {
  if (!fim) return false
  return ultimoDiaCoberto ? dia <= fim : dia < fim
}

/** §5.2 antes de salvar: Coberto se o dia do atendimento ≤ fim do tipo informado. */
export function previaCobertura(
  g: Pick<GarantiaLinha, 'data_inicio' | 'fim_hardware' | 'fim_software'>,
  tipo: TipoProblema,
  quando: string | null,
  ultimoDiaCoberto = true,
): { cobertura: Cobertura; fim: string | null; dia: string | null } {
  if (!g.data_inicio) return { cobertura: 'sem_data_de_entrega', fim: null, dia: diaEmSP(quando) }
  const fim = tipo === 'hardware' ? g.fim_hardware : g.fim_software
  const dia = diaEmSP(quando) || hojeSP()
  return { cobertura: coberto(fim, dia, ultimoDiaCoberto) ? 'coberto' : 'fora_da_garantia', fim, dia }
}

type MsgLike = { id: string; autor: string; origem?: string | null; enviada_em: string | null; anexos?: unknown[] | null; texto?: string | null; tipo?: string | null }

function ts(m: MsgLike): number {
  const t = m.enviada_em ? new Date(m.enviada_em).getTime() : Number.NaN
  return Number.isNaN(t) ? 0 : t
}

/** As mensagens que o vínculo copiaria: as escolhidas ou, sem escolha, as mais recentes sem as de sistema (como o backend). */
export function mensagensDoVinculo<T extends MsgLike>(msgs: T[], escolhidas: string[], limite = 100): T[] {
  const ids = new Set(escolhidas)
  const lista = ids.size
    ? msgs.filter((m) => ids.has(m.id))
    : msgs.filter((m) => m.autor !== 'sistema')
  const ordem = [...lista].sort((a, b) => ts(a) - ts(b))
  return ids.size ? ordem : ordem.slice(-limite)
}

/**
 * A 1ª mensagem do cliente no TRECHO ATUAL da conversa (services/garantia
 * .inicio_do_trecho): o trecho termina na última do cliente e volta enquanto a
 * pausa entre uma mensagem e a anterior for de até `diasPausa` dias (a loja
 * respondendo no meio mantém o trecho vivo). null = nenhuma do cliente.
 */
export function inicioDoTrecho(ordem: MsgLike[], diasPausa = REGRAS_PADRAO.dias_pausa_atendimento): string | null {
  let ultima = -1
  ordem.forEach((m, i) => { if (m.autor === 'cliente' && m.enviada_em) ultima = i })
  if (ultima < 0) return null
  const pausa = diasPausa * 86_400_000
  let inicio = ultima
  while (inicio > 0 && ts(ordem[inicio]) - ts(ordem[inicio - 1]) <= pausa) inicio--
  const primeira = ordem.slice(inicio, ultima + 1).find((m) => m.autor === 'cliente' && m.enviada_em)
  return (primeira?.enviada_em as string) ?? null
}

/**
 * Data/hora do atendimento (o relógio da plataforma, como o backend):
 * mensagens escolhidas → a 1ª do cliente entre elas; sem escolha → a 1ª do
 * cliente no trecho atual da conversa (NÃO a última: a reclamação aberta no
 * prazo não vira "Fora" porque o vídeo chegou depois do fim). Sem mensagem do
 * cliente: a 1ª escolhida / a última da conversa.
 */
export function dataDoAtendimento(msgs: MsgLike[], escolhidas: string[], ultimaDaConversa: string | null, agora = Date.now(), diasPausa = REGRAS_PADRAO.dias_pausa_atendimento): string {
  const copiadas = mensagensDoVinculo(msgs, escolhidas)
  const doCliente = copiadas.filter((m) => m.autor === 'cliente' && m.enviada_em)
  if (escolhidas.length) {
    if (doCliente.length) return doCliente[0].enviada_em as string
    if (copiadas.length && copiadas[0].enviada_em) return copiadas[0].enviada_em
    return new Date(agora).toISOString()
  }
  return inicioDoTrecho(copiadas, diasPausa) || ultimaDaConversa || new Date(agora).toISOString()
}

export type AnexoDaConversa = { mensagem_id: string; tipo: string; url: string | null; nome: string | null }

/** Fotos, vídeos e arquivos das mensagens (sem os cartões de pedido/produto) — o que o vínculo tenta copiar. */
export function anexosDasMensagens(msgs: MsgLike[]): AnexoDaConversa[] {
  const saida: AnexoDaConversa[] = []
  for (const m of msgs) {
    for (const a of m.anexos || []) {
      if (!a || typeof a !== 'object') continue
      const o = a as Record<string, unknown>
      const tipo = String(o.tipo || 'arquivo')
      if (tipo === 'produto' || tipo === 'pedido') continue
      const url = typeof o.url === 'string' && o.url ? o.url : null
      const nome = typeof o.nome === 'string' && o.nome ? o.nome : typeof o.arquivo === 'string' && o.arquivo ? o.arquivo : null
      if (url || nome) saida.push({ mensagem_id: m.id, tipo, url, nome })
    }
  }
  return saida
}

export function ehImagem(a: { tipo?: string | null; content_type?: string | null; url?: string | null; nome?: string | null }): boolean {
  const t = `${a.tipo || ''} ${a.content_type || ''}`.toLowerCase()
  return t.includes('imag') || t.includes('image/') || /\.(png|jpe?g|webp|gif)(\?|$)/i.test(a.url || a.nome || '')
}

// ─── barra de dias restantes (§4.2) ─────────────────────────────────────────
export type NivelPrazo = 'folga' | 'perto' | 'acabou' | 'aguardando'
export function barraPrazo(p: Prazo | null | undefined): { pct: number; texto: string; nivel: NivelPrazo } {
  if (!p) return { pct: 0, texto: 'aguardando a data de entrega', nivel: 'aguardando' }
  const total = Math.max(1, p.dias_total)
  const pct = Math.max(0, Math.min(100, Math.round((p.dias_restantes / total) * 100)))
  if (!p.coberto_hoje) return { pct: 0, texto: `terminou em ${dataBR(p.fim)}`, nivel: 'acabou' }
  const d = p.dias_restantes
  const texto = d === 0 ? 'último dia hoje' : d === 1 ? '1 dia restante' : `${d} dias restantes`
  return { pct: Math.max(pct, 2), texto, nivel: d <= 30 ? 'perto' : 'folga' }
}

// ─── lista ──────────────────────────────────────────────────────────────────
export type FiltrosLista = {
  busca: string
  status: FiltroStatus
  periodo: 'cadastro' | 'entrega'
  de: string
  ate: string
  com_atendimento_no_mes: boolean
}

export const FILTROS_PADRAO: FiltrosLista = { busca: '', status: 'todos', periodo: 'cadastro', de: '', ate: '', com_atendimento_no_mes: false }

export const LIMITE_PAGINA = 100

export type CorpoLista = {
  limite: number
  offset: number
  busca?: string
  status?: FiltroStatus
  periodo?: 'cadastro' | 'entrega'
  de?: string
  ate?: string
  com_atendimento_no_mes?: boolean
}

/**
 * POST /api/garantias/lista — os filtros vão no CORPO: a busca leva nome e
 * CPF, e o log de acesso (uvicorn/Caddy) grava o endereço com a query.
 */
export function corpoDaLista(f: FiltrosLista, offset = 0, limite = LIMITE_PAGINA): CorpoLista {
  const q: CorpoLista = { limite, offset }
  const busca = f.busca.trim()
  if (busca) q.busca = busca
  if (f.status && f.status !== 'todos') q.status = f.status
  if (f.de || f.ate) {
    q.periodo = f.periodo
    if (f.de) q.de = f.de
    if (f.ate) q.ate = f.ate
  }
  if (f.com_atendimento_no_mes) q.com_atendimento_no_mes = true
  return q
}

// Os 4 cartões do §4.1 — clicar filtra a lista.
export type Cartao = 'ativas' | 'somente_software' | 'hw_vence_30d' | 'atendimentos_no_mes'
export const CARTOES: { chave: Cartao; rotulo: string; dica: string }[] = [
  { chave: 'ativas', rotulo: 'Ativas', dica: 'hardware e software cobertos hoje' },
  { chave: 'somente_software', rotulo: 'Só software', dica: 'o hardware acabou, o software continua' },
  { chave: 'hw_vence_30d', rotulo: 'HW vence em 30d', dica: 'ativas cujo hardware termina nos próximos 30 dias' },
  { chave: 'atendimentos_no_mes', rotulo: 'Atendimentos no mês', dica: 'garantias com atendimento vinculado neste mês' },
]

/** O filtro que o cartão liga (clicar de novo desliga). */
export function filtroDoCartao(f: FiltrosLista, c: Cartao): FiltrosLista {
  const ativo = cartaoAtivo(f) === c
  if (c === 'atendimentos_no_mes') return { ...f, status: 'todos', com_atendimento_no_mes: !ativo }
  const status: FiltroStatus = c === 'ativas' ? 'ativa' : c
  return { ...f, status: ativo ? 'todos' : status, com_atendimento_no_mes: false }
}
export function cartaoAtivo(f: FiltrosLista): Cartao | null {
  if (f.com_atendimento_no_mes) return 'atendimentos_no_mes'
  if (f.status === 'ativa') return 'ativas'
  if (f.status === 'somente_software' || f.status === 'hw_vence_30d') return f.status
  return null
}

/** "123456" / "NF 10234 · série 1". */
export function textoNf(numero: string | null | undefined, serie?: string | null): string {
  if (!numero) return '—'
  return serie ? `${numero} · série ${serie}` : numero
}

export function resumir(texto: string | null | undefined, n = 140): string {
  const t = (texto || '').replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

export function tamanhoLegivel(bytes: number | null | undefined): string {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} MB`
}

// ─── cadastro e correção (§3) ───────────────────────────────────────────────
export type FormGarantia = { pedido: string; nf_numero: string; nf_serie: string; cliente_nome: string; cpf: string }

const RX_NF = /^[0-9.\s/-]+$/

/**
 * Confere o formulário antes de mandar (o backend confere de novo). Na
 * correção (`edicao`), CPF vazio = mantém o atual. Devolve os erros por
 * campo — vazio = pode salvar.
 */
export function validarFormulario(f: FormGarantia, edicao = false): Record<string, string> {
  const e: Record<string, string> = {}
  if (!f.pedido.trim()) e.pedido = 'Informe o nº do pedido.'
  const nf = f.nf_numero.trim()
  const nfDig = soDigitos(nf).replace(/^0+/, '')
  if (!nf) e.nf_numero = 'Informe a NF.'
  else if (!RX_NF.test(nf) || !nfDig || nfDig.length > 9) e.nf_numero = 'NF inválida — use só números (até 9 dígitos).'
  const serie = f.nf_serie.trim()
  if (serie && (!RX_NF.test(serie) || soDigitos(serie).replace(/^0+/, '').length > 3)) e.nf_serie = 'Série inválida — até 3 números.'
  const nome = f.cliente_nome.replace(/\s+/g, ' ').trim()
  if (nome.length < 3) e.cliente_nome = nome ? 'Mínimo de 3 letras.' : 'Informe o nome do cliente.'
  else if (nome.length > 200) e.cliente_nome = 'Máximo de 200 caracteres.'
  if (!(edicao && !soDigitos(f.cpf))) {
    const ec = erroDoCpf(f.cpf)
    if (ec) e.cpf = ec
  }
  return e
}

/** POST /api/garantias — só o que é INFORMADO (RN01: datas e status nunca vão). */
export function corpoDoCadastro(f: FormGarantia): { pedido: string; nf_numero: string; nf_serie?: string; cliente_nome: string; cpf: string } {
  const corpo: { pedido: string; nf_numero: string; nf_serie?: string; cliente_nome: string; cpf: string } = {
    pedido: f.pedido.trim(),
    nf_numero: f.nf_numero.trim(),
    cliente_nome: f.cliente_nome.replace(/\s+/g, ' ').trim(),
    cpf: soDigitos(f.cpf),
  }
  if (f.nf_serie.trim()) corpo.nf_serie = f.nf_serie.trim()
  return corpo
}

/** PUT /api/garantias/{id} — só os campos que mudaram (cada um vira linha no log). */
export function corpoDaCorrecao(original: Pick<GarantiaDetalhe, 'pedido_bling' | 'pedido_marketplace' | 'nf_numero' | 'nf_serie' | 'cliente_nome' | 'cpf' | 'cpf_completo'>, f: FormGarantia): Record<string, string> {
  const corpo: Record<string, string> = {}
  const pedido = f.pedido.trim()
  if (pedido && pedido !== original.pedido_bling && pedido !== (original.pedido_marketplace || '')) corpo.pedido = pedido
  const nf = soDigitos(f.nf_numero).replace(/^0+/, '')
  if (nf && nf !== original.nf_numero) corpo.nf_numero = f.nf_numero.trim()
  const serie = soDigitos(f.nf_serie).replace(/^0+/, '')
  if (serie !== (original.nf_serie || '')) corpo.nf_serie = f.nf_serie.trim()
  const nome = f.cliente_nome.replace(/\s+/g, ' ').trim()
  if (nome && nome !== original.cliente_nome) corpo.cliente_nome = nome
  const cpf = soDigitos(f.cpf)
  if (cpf && !(original.cpf_completo && soDigitos(original.cpf) === cpf)) corpo.cpf = cpf
  return corpo
}

/** "NF 10234 · série 1 · produto · R$ 1.299,00" — o botão de escolher a nota do pedido. */
export function rotuloNota(n: NotaDoPedido): string {
  const partes = [`NF ${n.numero}`]
  if (n.serie) partes.push(`série ${n.serie}`)
  if (n.papel === 'produto') partes.push('produto')
  else if (n.papel === 'embalagem') partes.push('embalagem')
  if (typeof n.valor === 'number') partes.push(n.valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }).replace(/\s/g, ' '))
  return partes.join(' · ')
}
