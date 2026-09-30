// Tipos e helpers da aba Cadastros › Emissão de Serviço (NFS-e).
//
// 29/09/2026: o motor passou a ser a NFE.io (a NFE.io assina, numera e fala com
// a prefeitura; não há mais senha do certificado). Teste × produção agora é de
// cada EMPRESA (o ambiente dela na NFE.io), não do servidor.
//
// 28/09/2026 (Eduardo: "to achando simples e bagunçado"): a tela foi refeita
// em cima de um contrato só (NfseTela, via provide/inject) e de um vocabulário
// só — situações, tons de cor, mensagens de erro e textos de pendência saem
// DAQUI, nenhum componente escolhe cor ou frase por conta própria.

import { inject, type Component, type ComputedRef, type InjectionKey, type Ref } from 'vue'
import {
  AlertCircle, Ban, CheckCircle2, Clock, HelpCircle, Loader2, MinusCircle, RotateCw, XCircle,
} from 'lucide-vue-next'

// ---------------------------------------------------------------------------
// Tipos da API (espelham apps/api/app/schemas/nfse.py)
// ---------------------------------------------------------------------------

// Dados da empresa que são NOSSOS (o resto — regime, inscrição municipal,
// município, certificado — vive no cadastro da empresa na NFE.io).
export type Fiscal = {
  city_service_code: string | null // código do serviço na prefeitura (padrão do grupo: "6303")
  federal_service_code: string | null // item da lista de serviços, LC 116 (padrão: "10.05")
  c_nbs: string | null
  retencao_ir: RetencaoIr
  email: string | null
  fone: string | null
}

// Retenção de IR: 'auto' segue a regra (Lucro Presumido + tomador empresa +
// IR acima de R$ 10,00); a contabilidade pode forçar sempre/nunca.
export type RetencaoIr = 'auto' | 'sempre' | 'nunca'

export type AmbienteNfeio = 'Production' | 'Development' | 'Staging'
export type RegimeNfeio = 'SimplesNacional' | 'LucroPresumido' | 'LucroReal'

// A empresa como está cadastrada na NFE.io (só leitura aqui).
export type Nfeio = {
  company_id: string // id da empresa na NFE.io
  link: string // https://app.nfe.io/companies/<id>
  ambiente: AmbienteNfeio | null
  teste: boolean // ambiente diferente de Produção: nota simulada
  status_fiscal: string | null // "Active", "CityNotSupported", "Pending"…
  regime: RegimeNfeio | null
  retem_ir: boolean // a regra já aplicada (regime + retencao_ir)
  inscricao_municipal: string | null
  municipio: string | null
  uf: string | null
  cert_status: 'Active' | 'Overdue' | null
  cert_expira: string | null // 'AAAA-MM-DD'
  sincronizado_em: string | null
}

export type Prestador = {
  company_id: string
  apelido: string
  razao_social: string
  cnpj: string | null
  fiscal: Fiscal | null
  nfeio: Nfeio | null // null = a empresa ainda não está ligada à NFE.io
  pronto: boolean
  pendencias: string[] // impedem a emissão (texto pronto para ler)
  avisos: string[] // não impedem (ex.: certificado vence em 10 dias)
  // 29/09 (Eduardo: "a porcentagem de cada empresa que temos"): a % PADRÃO das
  // notas de percentual que a empresa emite, cadastrada em Cadastros › Empresas.
  // "0.5000" = 0,5%; null = a empresa não tem (cada nota fixa precisa da sua).
  percentual_servico: string | null
}

// GET /api/nfse/status
export type StatusNfse = {
  provedor: 'nfeio'
  chave_configurada: boolean // a chave de acesso da NFE.io está no servidor
  producao_liberada: boolean // false = só empresas em Teste emitem daqui
}

// POST /api/nfse/nfeio/sincronizar (admin)
export type Sincronizacao = {
  ligadas: { company_id: string; apelido: string; nfeio_nome: string; ambiente: AmbienteNfeio | null }[]
  so_na_nfeio: { nfeio_id: string; nome: string; cnpj: string | null; ambiente: AmbienteNfeio | null }[]
  so_no_davinci: { company_id: string; apelido: string; cnpj: string | null }[]
}

export type Tomador = {
  id: string
  tipo: 'grupo' | 'externo'
  company_id: string | null
  documento: string | null
  nome: string | null
  email: string | null
  fone: string | null
  cep: string | null
  cmun_ibge: string | null
  logradouro: string | null
  numero: string | null
  complemento: string | null
  bairro: string | null
  ativo: boolean
  nome_nota: string | null
  documento_nota: string | null
  // 30/09: conta Bling de NF (lista automática, vem das notas de produto
  // emitidas). null = tomador cadastrado à mão.
  conta_bling?: ContaBling | null
}

export type ContaBling = { notas_mes: number; notas_90d: number; primeira: string | null; ultima: string | null }

export type Modelo = {
  id: string
  company_id: string
  tomador_id: string
  nome: string
  descricao: string
  // 29/09 (Eduardo: "quero emitir uma nota de serviço de 0,5%"): nota fixa pode ser
  // de valor fixo ou de percentual sobre uma base digitada na hora de emitir.
  tipo_valor: 'fixo' | 'percentual'
  valor: string | null // só 'fixo'
  // Só 'percentual': "0.5000" = 0,5%. null = usa a % da empresa que emite
  // (Prestador.percentual_servico) — ver pctDoModelo().
  percentual: string | null
  base_padrao: string | null // base sugerida (opcional) para o 'percentual'
  // Códigos do serviço só desta nota fixa (null = usa os da empresa).
  city_service_code: string | null
  federal_service_code: string | null
  c_nbs: string | null
  inf_comp: string | null
  ativo: boolean
  ordem: number
  prestador_nome: string | null
  tomador_nome: string | null
}

export type Msg = { codigo?: string; descricao?: string; complemento?: string; o_que_fazer?: string }

export type StatusEmissao =
  | 'enviando' | 'processando' | 'incerta' | 'emitida' | 'rejeitada' | 'cancelando' | 'cancelada'

export type Emissao = {
  id: string
  company_id: string
  tomador_id: string | null
  modelo_id: string | null
  competencia: string
  // 'processando' (29/09, NFE.io): a NFE.io recebeu e espera a prefeitura.
  status: StatusEmissao
  provedor: 'nfeio'
  nfeio_id: string | null
  nfeio_ambiente: string | null
  teste: boolean // empresa em Teste na NFE.io: nota simulada, sem valor fiscal
  flow_status: string | null
  flow_message: string | null // o que a NFE.io/prefeitura respondeu por último
  check_code: string | null // código de verificação da nota
  rps_serie: string | null
  rps_numero: number | null
  ir_retido: string | null
  descricao: string
  valor_servico: string
  base_calculo: string | null // nota de percentual: a base digitada
  percentual: string | null // nota de percentual: "0.5000" = 0,5%
  chave_acesso: string | null
  n_nfse: string | null
  dh_emi: string | null
  dh_proc: string | null
  v_bc: string | null
  p_aliq_aplic: string | null
  v_issqn: string | null
  v_liq: string | null
  snapshot: Record<string, any>
  alertas: Msg[] | null
  erros: Msg[] | null
  tentativas: number
  enviado_em: string | null
  cancelada_em: string | null
  created_at: string
  prestador_nome: string | null
  tomador_nome: string | null
}

// IR retido na prévia. `motivo` já vem escrito para ler ("Simples Nacional:
// não retém", "IR menor que R$ 10,00: não retém"…).
export type IrPrevia = { retem: boolean; aliquota: string | null; valor: string | null; motivo: string }

// Nota que já existe na NFE.io para o mesmo tomador no mês (emitida pelo painel
// da NFE.io, por exemplo). Aviso, não bloqueio.
export type DuplicadaNfeio = { numero: string | number | null; valor: string | number | null; emitida_em: string | null; status: string | null }

export type ItemPrevia = {
  modelo_id: string | null
  company_id?: string
  prestador?: { id: string; nome: string; cnpj: string | null }
  tomador?: { id: string; nome: string; documento: string; tipo: string }
  descricao?: string // já com o bloco de retenções quando retém IR
  valor?: string | null // já calculado quando é percentual (null = falta a base)
  tipo_valor?: 'fixo' | 'percentual'
  percentual?: string | null
  // De onde veio o % (percentual): o que veio no item, o da nota fixa ou o da
  // empresa que emite. null = nota de valor fixo, ou sem % nenhuma.
  percentual_origem?: OrigemPct | null
  base_calculo?: string | null
  // 30/09: de onde veio a base (percentual) e o faturamento do mês da empresa.
  base_origem?: OrigemBase | null
  faturamento?: FaturamentoMes | null
  city_service_code?: string | null
  federal_service_code?: string | null
  c_nbs?: string | null
  ambiente?: AmbienteNfeio | null
  teste?: boolean
  ir?: IrPrevia | null
  valor_liquido?: string | null
  problemas: string[]
  avisos: string[]
  ja_emitida: { id: string; status: string; n_nfse: string | null } | null
  duplicadas_nfeio?: DuplicadaNfeio[]
}

// ---------------------------------------------------------------------------
// Tipos da tela
// ---------------------------------------------------------------------------

// De onde vem o % de uma nota de percentual (igual ao backend, prévia).
export type OrigemPct = 'item' | 'nota_fixa' | 'empresa'

// Base da nota de percentual (Eduardo, 30/09: "faz com base no faturamento já"):
// o faturamento do mês da empresa (todas as lojas com o CNPJ dela, mesma régua
// da aba Faturamento), a base digitada, ou a sugerida da nota fixa (reserva).
export type OrigemBase = 'faturamento' | 'digitada' | 'nota_fixa'
export type FaturamentoLoja = { plataforma: string; conta: string | null; pedidos: number; valor: string }
export type FaturamentoMes = { valor: string; pedidos: number; lojas: FaturamentoLoja[] }
export type FaturamentoEmpresa = FaturamentoMes & { company_id: string }

// "R$ 593.978,00 · 716 pedidos (shopee, tiktok)" — de onde veio a base.
export function textoFaturamento(f: FaturamentoMes | null | undefined, mesNome: string): string {
  if (!f) return 'empresa sem loja cadastrada com o CNPJ dela (Cadastros › Lojas)'
  const lojas = f.lojas.filter((l) => Number(l.valor) > 0).map((l) => l.plataforma)
  const onde = lojas.length ? ` (${[...new Set(lojas)].join(', ')})` : ''
  return `faturamento de ${mesNome}: ${fmtBrl(f.valor)} · ${f.pedidos} ${f.pedidos === 1 ? 'pedido' : 'pedidos'}${onde}`
}
export type AbaId = 'emitir' | 'notas' | 'cadastros' | 'empresas'
export type Tom = 'sucesso' | 'atencao' | 'perigo' | 'info' | 'neutro'
// Seções da gaveta da empresa: o cartão da NFE.io e o serviço prestado.
export type SecaoEmpresa = 'nfeio' | 'servico'

// Estado de uma linha do "Emitir do mês"
export type EstadoLinha =
  | 'carregando' | 'pronta' | 'pronta_aviso' | 'pendencia' | 'valor_invalido'
  | 'recusada_antes' | 'cancelada_antes'
  | 'emitida' | 'processando' | 'incerta' | 'enviando' | 'cancelando'
// Estado de um item dentro do lote
export type EstadoLote =
  | 'fila' | 'enviando' | 'processando' | 'emitida' | 'rejeitada' | 'incerta' | 'nao_enviada' | 'desconhecido'
// Tudo que o chip NfseSituacao sabe mostrar
export type EstadoNota = StatusEmissao | EstadoLinha | EstadoLote

export type SituacaoInfo = { rotulo: string; icone: Component; tom: Tom; girar?: boolean; explicacao: string }

export type AlvoCorrecao = { tipo: 'empresa' | 'cadastro' | 'tomador' | 'modelo'; id: string; foco?: SecaoEmpresa }
export type ProblemaExplicado = {
  texto: string // sem "(E0713)", sem "(aba Empresas)", sem "(Cadastros › Empresas)"
  codigo: string | null // "E0713"
  alvo: 'empresa' | 'cadastro' | 'tomador' | 'modelo' | null
  foco?: SecaoEmpresa
}

export type ItemIn = {
  modelo_id?: string
  company_id?: string
  tomador_id?: string
  descricao?: string
  valor?: string
  // Percentual: manda base_calculo (e percentual, se não for o da nota fixa); o servidor
  // calcula o valor = base × % ÷ 100, arredondado no centavo.
  base_calculo?: string
  percentual?: string
  // Códigos do serviço só desta nota (vazio = os da empresa).
  city_service_code?: string | null
  federal_service_code?: string | null
  c_nbs?: string | null
  inf_comp?: string | null
}
export type ItemLote = {
  chave: string // modelo_id | 'avulsa' | 'reenvio:<emissao_id>'
  tipo: 'emitir' | 'reenviar'
  company_id: string
  empresa: string // apelido de quem emite
  tomador: string // nome que vai na nota
  titulo: string // nome da nota fixa | 'Nota avulsa' | 'Nota recusada de setembro/2026'
  valor: string // decimal '1500.00' (no percentual: o valor já calculado)
  base_calculo?: string // percentual: a base digitada
  percentual?: string // percentual: "0.5000"
  percentual_origem?: OrigemPct | null // percentual: "da empresa" aparece junto do %
  item?: ItemIn // tipo 'emitir'
  emissao_id?: string // tipo 'reenviar'
  reenvio?: boolean // recusada antes ou reenvio → chip "vai de novo"
  avisos?: string[] // da prévia; o passo 1 mostra sem repetir
  avulsa?: boolean // reenvio de nota avulsa: as informações complementares não vão de novo
  // Da prévia (ou da nota, no reenvio): empresa em Teste na NFE.io (nota
  // simulada) e o IR retido. undefined = não sabemos (vale o da empresa).
  teste?: boolean
  ir?: IrPrevia | null
  valor_liquido?: string | null
}
export type ResultadoLote = {
  chave: string
  estado: EstadoLote
  texto: string // frase pronta para a lista
  emissao?: Emissao
  erros?: Msg[] | null
  problemas?: string[] // 422 dados_invalidos
  motivo?: 'parou' | 'erro' // quando nao_enviada
  codigo?: string | null // código do erro da API (ex.: nao_ligada), quando nao_enviada
}

export type ConfirmarOpts = {
  titulo: string
  texto?: string
  linhas?: string[]
  tom?: 'padrao' | 'perigo'
  botao: string
  voltar?: string // padrão: 'Voltar'
  digitar?: string // ex.: 'EMITIR' → só confirma digitando
}
export type AbrirModeloOpts = { modelo?: Modelo | null; preset?: Partial<ModeloForm>; titulo?: string }
export type AbrirTomadorOpts = { tomador?: Tomador | null; preset?: Partial<TomadorForm> }

export type ModeloForm = {
  id?: string
  company_id: string | null
  tomador_id: string | null
  nome: string
  descricao: string
  tipo_valor: 'fixo' | 'percentual'
  valor: string
  percentual: string // '' no percentual = usa a % da empresa que emite
  base_padrao: string
  city_service_code: string | null
  federal_service_code: string | null
  c_nbs: string | null
  inf_comp: string | null
  ativo: boolean
  ordem: number
}
export type TomadorForm = {
  id?: string
  tipo: 'grupo' | 'externo'
  company_id: string | null
  documento: string | null
  nome: string | null
  email: string | null
  fone: string | null
  cep: string | null
  cmun_ibge: string | null
  logradouro: string | null
  numero: string | null
  complemento: string | null
  bairro: string | null
  ativo: boolean
}
export type EventoNota = {
  id: string
  tipo_evento: string
  c_motivo: number | null
  x_motivo: string | null
  status: string
  erros: Msg[] | null
  created_at: string
}
export type ChecklistItem = {
  chave: string
  titulo: string
  ok: boolean | null
  detalhe?: string
  acao?: string
  naoSalvo?: boolean
}
export type MenuItem = {
  id: string
  rotulo: string
  icone?: Component
  perigo?: boolean
  disabled?: boolean
  separar?: boolean // separador antes do item
  href?: string
  download?: boolean
  externo?: boolean
}

// ---------------------------------------------------------------------------
// Contrato da tela (provide/inject)
// ---------------------------------------------------------------------------

export interface NfseTela {
  // estado (as abas só leem; contadorEmitir e mes também podem ser escritos)
  // A ligação do servidor com a NFE.io (chave e trava de produção). O teste ×
  // produção é de cada empresa (Prestador.nfeio.teste).
  status: Ref<StatusNfse | null>
  erroStatus: Ref<string | null> // o /status falhou: ninguém emite até saber
  prestadores: Ref<Prestador[]>
  tomadores: Ref<Tomador[]>
  modelos: Ref<Modelo[]>
  carregado: Ref<boolean>
  carregando: Ref<boolean>
  versao: Ref<number> // sobe a cada recarregar()
  mes: Ref<string> // 'AAAA-MM' do Emitir (espelha ?mes=)
  contadorEmitir: Ref<number | null>
  canEdit: ComputedRef<boolean> // useCan('emissao_servico','edit')
  canDelete: ComputedRef<boolean> // useCan('emissao_servico','delete')
  isAdmin: ComputedRef<boolean> // useIsAdmin()
  podeAbrirCadastroEmpresa: ComputedRef<boolean> // useCan('empresa','view') → link /companies/{id}
  // useCan('empresa','edit'): pode mudar a ficha (ex.: a % da empresa). Só o
  // 'view' abre a ficha mas não edita: o convite é "ver", não "editar".
  podeEditarCadastroEmpresa: ComputedRef<boolean>
  // ações
  recarregar(): Promise<void>
  // Relê o /status e as empresas sem recarregar a tela toda (antes de mandar
  // notas: a empresa pode ter ido de Teste para Produção na NFE.io com a
  // página aberta). null = não deu para saber.
  conferirEmpresas(): Promise<Prestador[] | null>
  irPara(aba: AbaId, extra?: Record<string, string | undefined>): void
  confirmar(o: ConfirmarOpts): Promise<boolean>
  abrirEmpresa(companyId: string, foco?: SecaoEmpresa): Promise<boolean> // true = salvou algo
  abrirModelo(o?: AbrirModeloOpts): Promise<Modelo | null>
  abrirTomador(o?: AbrirTomadorOpts): Promise<Tomador | null>
  abrirNota(e: Emissao | string): Promise<void> // resolve ao fechar
  abrirAvulsa(o?: { competencia?: string }): Promise<void>
  emitirLote(o: { competencia: string; itens: ItemLote[] }): Promise<ResultadoLote[]>
  // "Atualizar da NFE.io": relê a nota lá e avisa o que mudou.
  conferir(e: Emissao): Promise<Emissao | null>
  // POST /emissoes/atualizar (sem aviso): o que veio, na ordem de `ids`.
  // null = não deu para falar com o servidor.
  atualizarEmissoes(ids: string[]): Promise<Emissao[] | null>
  reenviar(e: Emissao): Promise<Emissao | null>
  cancelar(e: Emissao): Promise<Emissao | null>
  enviarEmail(e: Emissao): Promise<boolean>
}

export const NFSE_TELA: InjectionKey<NfseTela> = Symbol('nfse-tela')

export function useNfseTela(): NfseTela {
  const t = inject(NFSE_TELA, null)
  if (!t) throw new Error('NfseTela ausente')
  return t
}

// O que cada janela expõe (defineExpose). A página guarda ref<XApi | null>(null).
export type ConfirmApi = { perguntar(o: ConfirmarOpts): Promise<boolean> }
export type LoteApi = {
  emitir(o: { competencia: string; itens: ItemLote[] }): Promise<ResultadoLote[]>
  ocupado(): boolean // true enquanto as notas estão saindo (não dá para sair da página)
}
export type AvulsaApi = { abrir(o?: { competencia?: string }): Promise<void> }
export type NotaApi = { abrir(e: Emissao | string): Promise<void> }
export type CancelarApi = { cancelar(e: Emissao): Promise<Emissao | null> }
export type ModeloApi = { abrir(o?: AbrirModeloOpts): Promise<Modelo | null> }
export type TomadorApi = { abrir(o?: AbrirTomadorOpts): Promise<Tomador | null> }
export type EmpresaApi = { abrir(companyId: string, foco?: SecaoEmpresa): Promise<boolean> }

// ---------------------------------------------------------------------------
// Constantes
// ---------------------------------------------------------------------------

// Regime da empresa como está na NFE.io.
export const REGIMES_NFEIO: Record<RegimeNfeio, string> = {
  SimplesNacional: 'Simples Nacional',
  LucroPresumido: 'Lucro Presumido',
  LucroReal: 'Lucro Real',
}

export function regimeTexto(r: string | null | undefined): string {
  if (!r) return 'não informado na NFE.io'
  return REGIMES_NFEIO[r as RegimeNfeio] ?? r
}

// Situação da empresa na prefeitura, pela NFE.io (status_fiscal).
const SITUACAO_FISCAL: Record<string, { texto: string; tom: Tom }> = {
  Active: { texto: 'Liberada para emitir', tom: 'sucesso' },
  CityNotSupported: { texto: 'A NFE.io não atende a prefeitura desta empresa', tom: 'perigo' },
  Pending: { texto: 'Cadastro pendente na NFE.io', tom: 'atencao' },
  Inactive: { texto: 'Desligada na NFE.io', tom: 'perigo' },
  None: { texto: 'Sem situação na NFE.io', tom: 'atencao' },
}

export function situacaoFiscalTexto(s: string | null | undefined): { texto: string; tom: Tom } {
  if (!s) return { texto: 'não informada', tom: 'neutro' }
  return SITUACAO_FISCAL[s] ?? { texto: s, tom: 'atencao' }
}

// Ambiente da empresa na NFE.io: Teste (nota simulada) ou Produção (de verdade).
export function ambienteTexto(a: string | null | undefined): string {
  if (a === 'Production') return 'Produção — nota de verdade'
  if (a === 'Development') return 'Teste — nota simulada, não vale como nota fiscal'
  if (a === 'Staging') return 'Homologação da prefeitura — nota de teste'
  return 'não informado'
}

// Texto do selo das notas e empresas de teste.
export const TEXTO_TESTE = 'TESTE — nota simulada, não vale como nota fiscal'

export const OPCOES_RETENCAO: { valor: RetencaoIr; titulo: string; descricao: string }[] = [
  {
    valor: 'auto',
    titulo: 'Automático (recomendado)',
    descricao:
      'Retém 1,5% de IR quando a empresa é do Lucro Presumido, o tomador é empresa e o IR passa de R$ 10,00.',
  },
  { valor: 'sempre', titulo: 'Sempre reter', descricao: 'Retém 1,5% de IR em toda nota desta empresa.' },
  { valor: 'nunca', titulo: 'Nunca reter', descricao: 'Nenhuma nota desta empresa retém IR.' },
]

// Igual ao backend (services/nfse/dps.py).
export const MESES: readonly string[] = [
  'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]

// Igual ao backend: nota viva trava outra do mesmo modelo no mês.
export const STATUS_VIVOS = ['enviando', 'processando', 'emitida', 'incerta', 'cancelando'] as const
export const STATUS_PARA_RESOLVER = ['rejeitada', 'incerta', 'enviando', 'processando', 'cancelando'] as const
// Ainda esperando a NFE.io ou a prefeitura: "Atualizar da NFE.io" resolve.
export const STATUS_ESPERANDO = ['enviando', 'processando', 'incerta', 'cancelando'] as const

export const TOM_PILL: Record<Tom, string> = {
  sucesso: 'pill-success',
  atencao: 'pill-warning',
  perigo: 'pill-danger',
  info: 'pill-info',
  neutro: 'pill-muted',
}

export const TOM_AVISO: Record<Tom, string> = {
  sucesso: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300',
  atencao: 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300',
  perigo: 'border-red-500/30 bg-red-500/10 text-red-800 dark:text-red-300',
  info: 'border-primary/30 bg-primary/5 text-foreground',
  neutro: 'border-border bg-muted/40 text-foreground',
}

export const TOM_TEXTO: Record<Tom, string> = {
  sucesso: 'text-emerald-600 dark:text-emerald-400',
  atencao: 'text-amber-600 dark:text-amber-400',
  perigo: 'text-red-600 dark:text-red-400',
  info: 'text-primary',
  neutro: 'text-muted-foreground',
}

// Selo "TESTE" das notas do ambiente de teste.
export const SELO_TESTE =
  'rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide bg-amber-500/15 text-amber-700 dark:text-amber-400'

export const SITUACOES: Record<EstadoNota, SituacaoInfo> = {
  emitida: {
    rotulo: 'Emitida', icone: CheckCircle2, tom: 'sucesso',
    explicacao: 'Autorizada pela prefeitura. PDF e XML disponíveis.',
  },
  rejeitada: {
    rotulo: 'Recusada', icone: XCircle, tom: 'perigo',
    explicacao: 'A nota foi recusada e não virou nota fiscal. Corrija o que foi apontado e reenvie.',
  },
  processando: {
    rotulo: 'Na prefeitura', icone: Loader2, tom: 'info', girar: true,
    explicacao:
      'A NFE.io recebeu a nota e está esperando a prefeitura autorizar. Costuma levar de segundos a alguns minutos. O DaVinci confere sozinho: não reenvie.',
  },
  incerta: {
    rotulo: 'Sem resposta', icone: HelpCircle, tom: 'atencao',
    explicacao: 'A NFE.io não confirmou se recebeu. Não reenvie: clique em "Atualizar da NFE.io" para saber se ela virou nota.',
  },
  enviando: {
    rotulo: 'Enviando', icone: Loader2, tom: 'info', girar: true,
    explicacao: 'O envio começou e não terminou. Se demorar, clique em "Atualizar da NFE.io".',
  },
  cancelando: {
    rotulo: 'Cancelando', icone: Clock, tom: 'atencao',
    explicacao: 'O cancelamento foi pedido e a prefeitura ainda não confirmou. Clique em "Atualizar da NFE.io".',
  },
  cancelada: {
    rotulo: 'Cancelada', icone: Ban, tom: 'neutro',
    explicacao: 'Nota cancelada na prefeitura. Ela não vale mais.',
  },
  carregando: {
    rotulo: 'Conferindo…', icone: Loader2, tom: 'neutro', girar: true,
    explicacao: 'Conferindo os dados da nota.',
  },
  pronta: {
    rotulo: 'Pronta', icone: CheckCircle2, tom: 'sucesso',
    explicacao: 'Pode emitir.',
  },
  pronta_aviso: {
    rotulo: 'Pronta', icone: CheckCircle2, tom: 'sucesso',
    explicacao: 'Pode emitir. Tem aviso, mas ele não impede.',
  },
  pendencia: {
    rotulo: 'Falta ajustar', icone: AlertCircle, tom: 'perigo',
    explicacao: 'Resolva antes de emitir.',
  },
  valor_invalido: {
    rotulo: 'Valor inválido', icone: AlertCircle, tom: 'perigo',
    explicacao: 'Digite um valor maior que zero.',
  },
  recusada_antes: {
    rotulo: 'Recusada antes', icone: RotateCw, tom: 'atencao',
    explicacao: 'Da última vez foi recusada. Ao emitir, a mesma nota vai de novo (não sai em dobro).',
  },
  cancelada_antes: {
    rotulo: 'Cancelada', icone: Ban, tom: 'neutro',
    explicacao: 'A nota deste mês foi cancelada. Pode emitir uma nova.',
  },
  fila: {
    rotulo: 'Na fila', icone: Clock, tom: 'neutro',
    explicacao: 'Esperando a vez.',
  },
  nao_enviada: {
    rotulo: 'Não enviada', icone: MinusCircle, tom: 'neutro',
    explicacao: 'Esta nota não foi enviada.',
  },
  desconhecido: {
    rotulo: 'Não sabemos se chegou', icone: HelpCircle, tom: 'atencao',
    explicacao: 'A conexão caiu durante o envio. Confira em Notas enviadas antes de tentar de novo.',
  },
}

// Mantido por compatibilidade: rótulo curto por status da emissão.
export const STATUS_LABEL: Record<StatusEmissao, string> = {
  enviando: SITUACOES.enviando.rotulo,
  processando: SITUACOES.processando.rotulo,
  emitida: SITUACOES.emitida.rotulo,
  rejeitada: SITUACOES.rejeitada.rotulo,
  incerta: SITUACOES.incerta.rotulo,
  cancelando: SITUACOES.cancelando.rotulo,
  cancelada: SITUACOES.cancelada.rotulo,
}

export const MOTIVOS_CANCELAMENTO: Record<1 | 2 | 9, { titulo: string; descricao: string; sugestoes: string[] }> = {
  1: {
    titulo: 'Erro na emissão',
    descricao: 'Valor, descrição ou tomador errados.',
    sugestoes: [
      'Nota emitida com valor errado.',
      'Nota emitida para o tomador errado.',
      'A descrição do serviço saiu errada.',
    ],
  },
  2: {
    titulo: 'Serviço não prestado',
    descricao: 'O serviço não aconteceu.',
    sugestoes: ['O serviço não foi prestado neste mês.'],
  },
  9: {
    titulo: 'Outro motivo',
    descricao: 'Explique na justificativa.',
    sugestoes: [],
  },
}

// Só é usada quando a API não manda `mensagem`.
export const MENSAGENS_ERRO: Record<string, string> = {
  forbidden: 'Você não tem permissão para isso.',
  escolha_a_empresa: 'Escolha a empresa do grupo.',
  nome_obrigatorio: 'Preencha o nome ou a razão social do tomador.',
  documento_invalido: 'CNPJ/CPF inválido: confira os números.',
  empresa_nao_encontrada: 'Empresa não encontrada. Atualize a página.',
  tomador_nao_encontrado: 'Tomador não encontrado. Atualize a página.',
  modelo_nao_encontrado: 'Nota fixa não encontrada. Atualize a página.',
  emissao_nao_encontrada: 'Nota não encontrada. Atualize a página.',
  tomador_igual_prestador: 'O tomador não pode ser a própria empresa que emite.',
  sem_xml: 'A NFE.io ainda não gerou o XML desta nota.',
  sem_nota: 'A nota ainda não foi gerada.',
  sem_dados_fiscais: 'Salve o serviço prestado da empresa antes.',
  ja_emitida: 'Essa nota fixa já tem nota neste mês.',
  nao_reenviavel: 'Só nota recusada pode ser reenviada.',
  producao_bloqueada: 'Esta empresa está em Produção na NFE.io e a emissão em produção está travada neste servidor.',
  nao_ligada: 'A empresa ainda não está ligada à NFE.io (aba Empresas).',
  chave_nfeio: 'A chave de acesso da NFE.io não está configurada ou não vale. Fale com o administrador.',
  avulsa_incompleta: 'A nota avulsa precisa de empresa, tomador, descrição e valor.',
  sem_percentual: 'Falta a porcentagem: defina na nota fixa ou na empresa (Cadastros › Empresas).',
}

// Pendência do backend (já em texto para ler) → onde se resolve. CNPJ se
// cadastra em Cadastros › Empresas; códigos do serviço, na gaveta da empresa;
// o resto é da ligação com a NFE.io (o cartão NFE.io da gaveta leva até lá).
export function pendenciaTexto(p: string): { texto: string; alvo: 'empresa' | 'cadastro'; foco?: SecaoEmpresa } {
  const t = (p || '').trim()
  if (/\bCNPJ\b/.test(t) && !/NFE\.?io/i.test(t)) return { texto: t, alvo: 'cadastro' }
  if (/c[óo]digo|servi[çc]o prestado|LC 116|NBS/i.test(t)) return { texto: t, alvo: 'empresa', foco: 'servico' }
  return { texto: t, alvo: 'empresa', foco: 'nfeio' }
}

// ---------------------------------------------------------------------------
// Situação / cor
// ---------------------------------------------------------------------------

export function situacao(s: EstadoNota | string): SituacaoInfo {
  return SITUACOES[s as EstadoNota] ?? { rotulo: String(s), icone: HelpCircle, tom: 'neutro', explicacao: '' }
}

export function statusPill(s: EstadoNota): string {
  return TOM_PILL[situacao(s).tom]
}

// Compatibilidade com o código antigo.
export function statusClass(s: string): string {
  return statusPill(s as EstadoNota)
}

// ---------------------------------------------------------------------------
// Formatação
// ---------------------------------------------------------------------------

export function fmtBrl(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) : String(v)
}

export function fmtDoc(v: string | null | undefined): string {
  const d = (v || '').replace(/\D/g, '')
  if (d.length === 14) return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`
  if (d.length === 11) return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`
  return v || '—'
}

// O tomador no estilo da lista da NFE.io (Eduardo, 30/09): raiz do CNPJ + nome
// em maiúsculas — "61.989.102 LEOMAR ALVES ANTUNES". Nome que já começa com a
// raiz (MEI: "62.570.297 PEDRO …") não repete. CPF: o CPF inteiro + nome.
export function tomadorEstiloNfeio(doc: string | null | undefined, nome: string | null | undefined): string {
  const d = (doc || '').replace(/\D/g, '')
  const n = (nome || '').trim().toUpperCase()
  if (d.length === 14) {
    const raiz = `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}`
    return n.startsWith(raiz) ? n : `${raiz} ${n}`.trim()
  }
  if (d.length === 11) return `${fmtDoc(d)} ${n}`.trim()
  return n
}

// O tomador gravado na nota enviada, no estilo da NFE.io.
export function tomadorDaEmissao(e: Pick<Emissao, 'snapshot' | 'tomador_nome'>): string {
  const t = e.snapshot?.tomador ?? {}
  const nome = t.nome || e.tomador_nome
  return nome ? tomadorEstiloNfeio(t.documento, nome) : '—'
}

// Documento e nome que vão na nota (do grupo: a empresa; de fora: o cadastro).
export function tomadorNaNota(t: Tomador | null | undefined): { doc: string | null; nome: string | null } {
  if (!t) return { doc: null, nome: null }
  return { doc: t.documento_nota || t.documento, nome: t.nome_nota || t.nome }
}

export function fmtData(v: string | null | undefined): string {
  if (!v) return '—'
  const [y, m, d] = v.slice(0, 10).split('-')
  return `${d}/${m}/${y}`
}

export function fmtCompetencia(v: string | null | undefined): string {
  if (!v) return '—'
  const [y, m] = v.slice(0, 7).split('-')
  return `${m}/${y}`
}

const p2 = (n: number) => String(n).padStart(2, '0')

// '2026-10-03T17:30:00Z' → '03/10/2026 14:30' (hora local)
export function fmtDataHora(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}

export function fmtHora(d: Date): string {
  return `${p2(d.getHours())}:${p2(d.getMinutes())}`
}

// plural(1, 'nota', 'notas') → '1 nota'; plural(3, ...) → '3 notas'
export function plural(n: number, um: string, varios: string): string {
  return `${n.toLocaleString('pt-BR')} ${n === 1 ? um : varios}`
}

// Para usar no meio da frase ("falta município"): baixa só a 1ª letra, e só
// quando a 2ª também é minúscula (ou é espaço), para não estragar "CNPJ".
export function minusculo(t: string): string {
  if (!t) return t
  const a = t[1]
  if (a === undefined || a === ' ' || (a === a.toLowerCase() && a !== a.toUpperCase())) {
    return t[0]!.toLowerCase() + t.slice(1)
  }
  return t
}

// ['município', 'regime'] → 'município e regime'; 3+ → 'a, b e c'
export function listaE(itens: string[]): string {
  if (itens.length <= 1) return itens[0] ?? ''
  return `${itens.slice(0, -1).join(', ')} e ${itens[itens.length - 1]}`
}

// ---------------------------------------------------------------------------
// Mês de competência
// ---------------------------------------------------------------------------

// "2026-09" ⇄ "2026-09-01" (API)
export function mesParaData(mes: string): string {
  return `${mes.slice(0, 7)}-01`
}

// O mês de competência é o de Brasília. O servidor (SSR) roda em UTC: sem o
// fuso, entre 21h e meia-noite do último dia do mês ele já estaria no mês
// seguinte e a tela "piscaria" ao hidratar no navegador.
let fmtAnoMes: Intl.DateTimeFormat | null = null

export function mesAtual(): string {
  try {
    fmtAnoMes ??= new Intl.DateTimeFormat('en-US', { timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit' })
    const partes = fmtAnoMes.formatToParts(new Date())
    const y = partes.find((x) => x.type === 'year')?.value
    const m = partes.find((x) => x.type === 'month')?.value
    if (y && m) return `${y}-${m}`
  } catch {
    // Sem fuso no Intl: cai no relógio local.
  }
  const d = new Date()
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}`
}

export function mesValido(v: unknown): v is string {
  return typeof v === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(v)
}

function partesMes(mes: string): [number, number] {
  const [y, m] = mes.slice(0, 7).split('-').map(Number)
  return [y || 0, m || 1]
}

// '2026-09' → 'setembro de 2026'
export function nomeMes(mes: string): string {
  if (!mes) return ''
  const [y, m] = partesMes(mes)
  return `${MESES[m - 1] ?? ''} de ${y}`
}

// '2026-09' ou '2026-09-01' → 'setembro/2026'
export function fmtMes(mes: string): string {
  if (!mes) return ''
  const [y, m] = partesMes(mes)
  return `${MESES[m - 1] ?? ''}/${y}`
}

// '2026-01', -1 → '2025-12'
export function somarMes(mes: string, delta: number): string {
  const [y, m] = partesMes(mes)
  const total = y * 12 + (m - 1) + delta
  return `${Math.floor(total / 12)}-${p2((total % 12) + 1)}`
}

// Igual ao backend (services/nfse/dps.py): {competencia} {mes_nome} {mes} {ano}.
export function renderDescricao(texto: string, mes: string): string {
  if (!texto) return ''
  if (!mes) return texto
  const [y, m] = partesMes(mes)
  return texto
    .split('{competencia}').join(`${p2(m)}/${y}`)
    .split('{mes_nome}').join(MESES[m - 1] ?? '')
    .split('{mes}').join(p2(m))
    .split('{ano}').join(String(y))
}

// Insere `token` onde está o cursor do textarea (ou no fim) e devolve o texto
// novo. O cursor volta para logo depois do token.
export function inserirNoCursor(el: HTMLTextAreaElement | null, atual: string, token: string): string {
  const texto = atual ?? ''
  if (!el) return texto && !/\s$/.test(texto) ? `${texto} ${token}` : texto + token
  const ini = el.selectionStart ?? texto.length
  const fim = el.selectionEnd ?? ini
  const novo = texto.slice(0, ini) + token + texto.slice(fim)
  const pos = ini + token.length
  setTimeout(() => {
    el.focus()
    el.setSelectionRange(pos, pos)
  })
  return novo
}

// ---------------------------------------------------------------------------
// Dinheiro / documento
// ---------------------------------------------------------------------------

// "1.500,00" / "1500,00" / "1500.00" / "1500" → "1500.00" (o que a API aceita).
// Sem vírgula, ponto em grupos de 3 é milhar: "200.000" → "200000.00" (200 mil,
// o jeito que o Eduardo escreve), "1.000.000" → "1000000.00". A API devolve
// dinheiro sempre com 2 casas ("200000.00"), então não confunde. Não é usado
// no percentual (lá "0.500" é 0,5%).
const MILHAR_BR = /^[1-9]\d{0,2}(\.\d{3})+$/

export function paraDecimal(v: string | number | null | undefined): string {
  let s = String(v ?? '').trim().replace(/[R$\s]/g, '')
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  else if (MILHAR_BR.test(s)) s = s.replace(/\./g, '')
  const n = Number(s)
  return Number.isFinite(n) && s !== '' ? n.toFixed(2) : ''
}

export function soDigitos(v: string | null | undefined): string {
  return (v || '').replace(/\D/g, '')
}

export function docTipo(v: string | null | undefined): 'CPF' | 'CNPJ' | null {
  const d = soDigitos(v)
  if (d.length === 11) return 'CPF'
  if (d.length === 14) return 'CNPJ'
  return null
}

function cpfValido(d: string): boolean {
  if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false
  for (const t of [9, 10]) {
    let s = 0
    for (let i = 0; i < t; i++) s += Number(d[i]) * (t + 1 - i)
    const dv = ((s * 10) % 11) % 10
    if (dv !== Number(d[t])) return false
  }
  return true
}

const PESOS_CNPJ = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

function cnpjValido(d: string): boolean {
  if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false
  const dv = (n: number) => {
    const pesos = PESOS_CNPJ.slice(13 - n)
    let s = 0
    for (let i = 0; i < n; i++) s += Number(d[i]) * (pesos[i] ?? 0)
    const r = s % 11
    return r < 2 ? 0 : 11 - r
  }
  return dv(12) === Number(d[12]) && dv(13) === Number(d[13])
}

// Dígitos verificadores do CPF (11) ou CNPJ (14).
export function docValido(v: string | null | undefined): boolean {
  const d = soDigitos(v)
  if (d.length === 11) return cpfValido(d)
  if (d.length === 14) return cnpjValido(d)
  return false
}

// Máscara progressiva ao digitar (CPF até 11 dígitos, CNPJ até 14).
export function mascaraDoc(v: string): string {
  const d = soDigitos(v).slice(0, 14)
  if (d.length <= 11) {
    let out = d.slice(0, 3)
    if (d.length > 3) out += '.' + d.slice(3, 6)
    if (d.length > 6) out += '.' + d.slice(6, 9)
    if (d.length > 9) out += '-' + d.slice(9, 11)
    return out
  }
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}${d.length > 12 ? '-' + d.slice(12) : ''}`
}

// ---------------------------------------------------------------------------
// Erros da API
// ---------------------------------------------------------------------------

export function codigoErro(e: any): string | null {
  const c = e?.data?.detail?.code
  return typeof c === 'string' ? c : null
}

function statusHttp(e: any): number | null {
  const s = e?.status ?? e?.statusCode ?? e?.response?.status
  return typeof s === 'number' && s > 0 ? s : null
}

// Frase para a pessoa ler. Nunca o código cru.
export function erroApi(e: any): string {
  const d = e?.data?.detail
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    if (typeof d.mensagem === 'string' && d.mensagem) return d.mensagem
    if (typeof d.code === 'string' && MENSAGENS_ERRO[d.code]) return MENSAGENS_ERRO[d.code]!
  }
  if (typeof d === 'string' && d) return MENSAGENS_ERRO[d] ?? d
  if (Array.isArray(d)) {
    const msgs = d
      .map((x: any) => (typeof x?.msg === 'string' ? x.msg.replace(/^Value error,\s*/i, '') : ''))
      .filter(Boolean)
    if (msgs.length) return msgs.join('; ')
  }
  if (e && typeof e === 'object' && !statusHttp(e) && (e.name === 'FetchError' || 'request' in e)) {
    return 'Não deu para falar com o servidor. Confira a internet e tente de novo.'
  }
  return 'Algo deu errado. Tente de novo.'
}

export function problemasApi(e: any): string[] {
  const p = e?.data?.detail?.problemas
  return Array.isArray(p) ? p.filter((x: unknown): x is string => typeof x === 'string') : []
}

const CAMPO_POR_CODIGO: Record<string, string> = {
  tomador_igual_prestador: 'tomador_id',
  escolha_a_empresa: 'company_id',
  documento_invalido: 'documento',
  nome_obrigatorio: 'nome',
  so_admin_troca_certificado: 'certificado_id',
  certificado_de_outra_empresa: 'certificado_id',
}

// Em que campo do formulário o erro deve aparecer (null = erro geral).
export function campoDoErro(e: any): string | null {
  const c = codigoErro(e)
  if (c && CAMPO_POR_CODIGO[c]) return CAMPO_POR_CODIGO[c]!
  const d = e?.data?.detail
  if (Array.isArray(d) && Array.isArray(d[0]?.loc)) {
    const ultimo = d[0].loc[d[0].loc.length - 1]
    return typeof ultimo === 'string' && ultimo !== 'body' ? ultimo : null
  }
  return null
}

// true = não houve resposta, ou 500/502/504 sem explicação: a nota PODE ter
// chegado à NFE.io. Erro com código da nossa API (detail.code, ex.:
// nao_ligada) acontece ANTES de gravar a emissão: a nota não saiu.
export function falhaDeRede(e: any): boolean {
  if (codigoErro(e) != null) return false
  const s = statusHttp(e)
  return s == null || s === 500 || s === 502 || s === 504
}

// ---------------------------------------------------------------------------
// Problemas da prévia → texto limpo + onde corrigir
// ---------------------------------------------------------------------------

export function explicarProblema(texto: string, ctx?: { modeloTemCodigos?: boolean }): ProblemaExplicado {
  const bruto = texto || ''
  const codigo = bruto.match(/\((E\d{4})\)/)?.[1] ?? null
  // Sem código, sem "(aba …)" e sem jargão: "empresa prestadora" vira "empresa".
  const limpo = bruto
    .replace(/\s*\(E\d{4}\)/g, '')
    .replace(/\s*\(aba Empresas\)/gi, '')
    .replace(/\s*\(Cadastros › Empresas\)/gi, '')
    .replace(/empresa prestadora/gi, 'empresa')
    .trim()
  const r = (alvo: ProblemaExplicado['alvo'], foco?: SecaoEmpresa): ProblemaExplicado =>
    foco ? { texto: limpo, codigo, alvo, foco } : { texto: limpo, codigo, alvo }

  // "Falta a porcentagem: defina na nota fixa ou na empresa (Cadastros ›
  // Empresas)" (29/09): sem o "(Cadastros › Empresas)" que a limpeza tira, "na
  // empresa" confunde com a aba Empresas daqui (onde a % é só leitura). Diz os
  // dois lugares certos; o conserto da linha abre a nota fixa.
  if (/falta a porcentagem/i.test(bruto)) {
    return {
      texto: 'Falta a porcentagem: digite a % na nota fixa ou cadastre a % padrão da empresa em Cadastros › Empresas.',
      codigo,
      alvo: 'modelo',
    }
  }
  // Já existe nota na NFE.io para o tomador no mês: só aviso, não há o que corrigir.
  if (/j[áa] existe nota na NFE\.?io/i.test(bruto)) return r(null)
  // Nota de percentual: {percentual}/{base} numa nota de valor fixo, ou a nota
  // fixa sem o % — o conserto é na própria nota fixa. ("dá menos de R$ 0,01"
  // fica sem alvo: quem resolve é a base digitada na linha.)
  if (/\{percentual\}|\{base\}|percentual da nota/i.test(bruto)) return r('modelo')
  if (/CNPJ da empresa|empresa (est[áa] )?sem CNPJ/i.test(bruto)) return r('cadastro')
  if (/tomador/i.test(bruto)) return r('tomador')
  if (/descri[çc][ãa]o|informa[çc][õo]es complementares|valor do servi[çc]o/i.test(bruto)) return r('modelo')
  if (/c[óo]digo (do servi[çc]o|municipal|de tributa[çc][ãa]o|NBS)|\bNBS\b|LC 116|lista de servi[çc]os/i.test(bruto)) {
    return ctx?.modeloTemCodigos ? r('modelo') : r('empresa', 'servico')
  }
  // Tudo que é da empresa na NFE.io (ligação, prefeitura, certificado, cadastro).
  if (/NFE\.?io|prefeitura|certificado|inscri[çc][ãa]o municipal|regime|n[ãa]o ligada/i.test(bruto)) {
    return r('empresa', 'nfeio')
  }
  if (/dados fiscais|servi[çc]o prestado|aba Empresas/i.test(bruto)) return r('empresa', 'servico')
  return r(null)
}

// ---------------------------------------------------------------------------
// Estado de uma linha do "Emitir do mês"
// ---------------------------------------------------------------------------

const VIVOS = new Set<string>(STATUS_VIVOS)

export const ESTADOS_MARCAVEIS: readonly EstadoLinha[] = ['pronta', 'pronta_aviso', 'recusada_antes', 'cancelada_antes']

export function estadoLinha(o: {
  previa: ItemPrevia | null | undefined
  emissoes: Emissao[] // do mês e do ambiente atual, deste modelo, created_at desc
  valor: string // valor digitado nesta linha
}): { estado: EstadoLinha; emissao: Emissao | null; anterior: Emissao | null; marcavel: boolean } {
  const lista = o.emissoes ?? []
  const viva = lista.find((e) => VIVOS.has(e.status))
  if (viva) return { estado: viva.status as EstadoLinha, emissao: viva, anterior: null, marcavel: false }

  const recusada = lista.find((e) => e.status === 'rejeitada') ?? null
  const cancelada = lista.find((e) => e.status === 'cancelada') ?? null
  const p = o.previa
  if (!p) return { estado: 'carregando', emissao: null, anterior: recusada ?? cancelada, marcavel: false }

  if (p.ja_emitida) {
    const s = p.ja_emitida.status
    return { estado: (VIVOS.has(s) ? s : 'emitida') as EstadoLinha, emissao: null, anterior: null, marcavel: false }
  }
  if (p.problemas?.length) return { estado: 'pendencia', emissao: null, anterior: recusada, marcavel: false }

  const v = paraDecimal(o.valor)
  if (!v || Number(v) <= 0) {
    return { estado: 'valor_invalido', emissao: null, anterior: recusada ?? cancelada, marcavel: false }
  }
  if (recusada) return { estado: 'recusada_antes', emissao: null, anterior: recusada, marcavel: true }
  if (cancelada) return { estado: 'cancelada_antes', emissao: null, anterior: cancelada, marcavel: true }
  if (p.avisos?.length) return { estado: 'pronta_aviso', emissao: null, anterior: null, marcavel: true }
  return { estado: 'pronta', emissao: null, anterior: null, marcavel: true }
}

// O que o lote mostra depois que a API devolveu a emissão.
export function resultadoDaEmissao(e: Emissao): Pick<ResultadoLote, 'estado' | 'texto' | 'erros'> {
  if (e.status === 'emitida') {
    const n = e.n_nfse ? ` nº ${e.n_nfse}` : ''
    return {
      estado: 'emitida',
      texto: e.teste ? `Nota de teste${n} emitida (simulada, sem valor fiscal)` : `Nota${n} emitida`,
      erros: null,
    }
  }
  if (e.status === 'processando' || e.status === 'enviando') {
    return { estado: 'processando', texto: 'Na prefeitura… esperando a autorização.', erros: null }
  }
  if (e.status === 'incerta') {
    return {
      estado: 'incerta',
      texto: 'A NFE.io não confirmou se recebeu. Não reenvie: o DaVinci confere sozinho.',
      erros: null,
    }
  }
  if (e.status === 'rejeitada') {
    const m = e.erros?.[0]
    const motivo = m?.o_que_fazer || m?.descricao || e.flow_message || 'veja o motivo em Notas enviadas.'
    return { estado: 'rejeitada', texto: `Recusada: ${motivo}`, erros: e.erros }
  }
  return {
    estado: 'desconhecido',
    texto: `A nota ficou como "${situacao(e.status).rotulo}". Confira em Notas enviadas.`,
    erros: e.erros,
  }
}

// IR da prévia numa frase: "IR retido R$ 48,79 (1,5%) · líquido R$ 3.203,56"
// ou o motivo de não reter ("IR menor que R$ 10,00: não retém").
export function textoIr(ir: IrPrevia | null | undefined, liquido?: string | null): string | null {
  if (!ir) return null
  if (ir.retem && ir.valor != null && ir.valor !== '') {
    const pct = ir.aliquota ? ` (${fmtPct(ir.aliquota)})` : ''
    return `IR retido ${fmtBrl(ir.valor)}${pct}${liquido ? ` · líquido ${fmtBrl(liquido)}` : ''}`
  }
  return ir.motivo || 'Sem retenção de IR'
}

// O item do lote que reenvia uma nota recusada. Usado pela página (Notas
// enviadas, detalhe da nota) e pela nota avulsa. Percentual: a base e o % da
// recusada vão junto só para o lote mostrar a conta — o servidor reenvia com os
// mesmos, gravados na nota.
export function itemReenvio(e: Emissao): ItemLote {
  const it: ItemLote = {
    chave: `reenvio:${e.id}`,
    tipo: 'reenviar',
    company_id: e.company_id,
    empresa: e.prestador_nome || 'Empresa',
    tomador: e.tomador_nome || '',
    titulo: `Nota recusada de ${fmtMes(e.competencia)}`,
    valor: e.valor_servico,
    emissao_id: e.id,
    reenvio: true,
    avulsa: !e.modelo_id,
    teste: e.teste,
  }
  if (e.percentual) {
    it.percentual = e.percentual
    it.percentual_origem = origemDaEmissao(e)
    if (e.base_calculo) it.base_calculo = e.base_calculo
  }
  return it
}

// ---------------------------------------------------------------------------
// Empresa (prestador) / certificado / tomador
// ---------------------------------------------------------------------------

export function prestadorPorId(lista: Prestador[], id: string | null | undefined): Prestador | null {
  if (!id) return null
  return lista.find((p) => p.company_id === id) ?? null
}

function diasAte(data: string): number {
  const [y, m, d] = data.slice(0, 10).split('-').map(Number)
  const alvo = Date.UTC(y || 0, (m || 1) - 1, d || 1)
  const h = new Date()
  const hoje = Date.UTC(h.getFullYear(), h.getMonth(), h.getDate())
  return Math.round((alvo - hoje) / 86_400_000)
}

// Certificado A1 da empresa, como está na NFE.io (quem assina é a NFE.io). Em
// Teste o certificado é opcional: vencido ou ausente não impede.
export function situacaoCertificado(p: Prestador): { rotulo: string; tom: Tom; dias: number | null; vencido: boolean } {
  const n = p?.nfeio
  if (!n) return { rotulo: 'empresa não ligada', tom: 'neutro', dias: null, vencido: false }
  const exp = n.cert_expira
  if (!exp && !n.cert_status) {
    return n.teste
      ? { rotulo: 'sem certificado (opcional no teste)', tom: 'neutro', dias: null, vencido: false }
      : { rotulo: 'sem certificado na NFE.io', tom: 'perigo', dias: null, vencido: true }
  }
  const dias = exp ? diasAte(exp) : null
  const vencido = n.cert_status === 'Overdue' || (dias != null && dias < 0)
  const tomVencido: Tom = n.teste ? 'neutro' : 'perigo'
  const sufixo = n.teste ? ' (opcional no teste)' : ''
  if (vencido) {
    return { rotulo: exp ? `venceu em ${fmtData(exp)}${sufixo}` : `vencido${sufixo}`, tom: tomVencido, dias, vencido: true }
  }
  if (dias == null) return { rotulo: 'válido (validade não informada)', tom: 'neutro', dias, vencido: false }
  if (dias === 0) return { rotulo: 'vence hoje', tom: 'atencao', dias, vencido: false }
  if (dias <= 30) return { rotulo: `vence em ${plural(dias, 'dia', 'dias')}`, tom: 'atencao', dias, vencido: false }
  return { rotulo: `vence ${fmtData(exp)}`, tom: 'neutro', dias, vencido: false }
}

// A empresa está em Teste na NFE.io (nota simulada)? null = não ligada.
export function empresaTeste(p: Prestador | null | undefined): boolean | null {
  return p?.nfeio ? !!p.nfeio.teste : null
}

type CampoEndereco = 'cep' | 'cmun_ibge' | 'logradouro' | 'numero' | 'bairro'
const CAMPOS_ENDERECO: [CampoEndereco, string][] = [
  ['cep', 'CEP'],
  ['cmun_ibge', 'código do município'],
  ['logradouro', 'rua'],
  ['numero', 'número'],
  ['bairro', 'bairro'],
]

// O endereço só vai na nota completo (5 campos).
export function enderecoTomador(t: Pick<Tomador, CampoEndereco>): {
  situacao: 'completo' | 'incompleto' | 'vazio'
  preenchidos: number
  faltando: string[]
} {
  const faltando: string[] = []
  let preenchidos = 0
  for (const [k, nome] of CAMPOS_ENDERECO) {
    if (String(t?.[k] ?? '').trim()) preenchidos++
    else faltando.push(nome)
  }
  const s = preenchidos === CAMPOS_ENDERECO.length ? 'completo' : preenchidos === 0 ? 'vazio' : 'incompleto'
  return { situacao: s, preenchidos, faltando }
}

export function modeloParaForm(m: Modelo): ModeloForm {
  return {
    id: m.id,
    company_id: m.company_id,
    tomador_id: m.tomador_id,
    nome: m.nome,
    descricao: m.descricao,
    // Nota fixa antiga (antes do percentual) não traz tipo_valor: é de valor fixo.
    tipo_valor: m.tipo_valor ?? 'fixo',
    valor: m.valor ?? '',
    percentual: m.percentual ?? '',
    base_padrao: m.base_padrao ?? '',
    city_service_code: m.city_service_code ?? null,
    federal_service_code: m.federal_service_code ?? null,
    c_nbs: m.c_nbs,
    inf_comp: m.inf_comp,
    ativo: m.ativo,
    ordem: m.ordem,
  }
}

export function tomadorParaForm(t: Tomador): TomadorForm {
  return {
    id: t.id,
    tipo: t.tipo,
    company_id: t.company_id,
    documento: t.documento,
    nome: t.nome,
    email: t.email,
    fone: t.fone,
    cep: t.cep,
    cmun_ibge: t.cmun_ibge,
    logradouro: t.logradouro,
    numero: t.numero,
    complemento: t.complemento,
    bairro: t.bairro,
    ativo: t.ativo,
  }
}


// ---------------------------------------------------------------------------
// Nota de percentual (29/09) e ajuda para preencher a empresa
// ---------------------------------------------------------------------------

// Mesma conta do servidor: base × % ÷ 100, arredondado no centavo (meio pra cima).
// Entradas no formato da API ("200000.00", "0.5"). Devolve "" se não der pra calcular.
// A conta é em inteiros, como o Decimal do servidor: em ponto flutuante, a conta
// que caía bem no meio centavo às vezes arredondava pra baixo e a tela mostrava
// 1 centavo a menos que a nota (ex.: R$ 262.144,10 × 15%).
export function calcularPercentual(base: string | null | undefined, pct: string | null | undefined): string {
  const b = Number(base)
  const p = Number(pct)
  if (!Number.isFinite(b) || !Number.isFinite(p) || b <= 0 || p <= 0) return ''
  // centavos × (% × 10.000) = o valor em milionésimos de centavo (a base tem
  // 2 casas e o %, até 4).
  const n = BigInt(Math.round(b * 100)) * BigInt(Math.round(p * 10000))
  const milhao = BigInt(1000000)
  const centavos = (n + milhao / BigInt(2)) / milhao // meio pra cima (só positivos)
  const cem = BigInt(100)
  return `${centavos / cem}.${String(centavos % cem).padStart(2, '0')}`
}

// "0.5000" → "0,5%" · "12.25" → "12,25%"
export function fmtPct(p: string | number | null | undefined): string {
  if (p == null || p === '') return '—'
  const n = Number(p)
  if (!Number.isFinite(n)) return String(p)
  return `${n.toLocaleString('pt-BR', { maximumFractionDigits: 4 })}%`
}

// --- A % da empresa (29/09, Eduardo: "a porcentagem de cada empresa que temos") ---
//
// Cadastros › Empresas guarda a % PADRÃO de cada empresa. Nota fixa de percentual
// sem % própria usa a da empresa que emite. Mesma ordem do servidor: o que vem
// no item (digitado na hora) → a da nota fixa → a da empresa → falta.

// "0.5000" → "0.5000"; vazio, zero ou lixo → null.
export function pctPositivo(v: string | number | null | undefined): string | null {
  if (v == null || v === '') return null
  return Number(v) > 0 ? String(v) : null
}

// A % que uma nota fixa usa hoje e de onde ela vem (sem o que se digita na hora).
export function pctDoModelo(
  m: Pick<Modelo, 'percentual' | 'company_id'>,
  prestadores: Prestador[],
): { pct: string | null; origem: OrigemPct | null } {
  const propria = pctPositivo(m.percentual)
  if (propria) return { pct: propria, origem: 'nota_fixa' }
  const daEmpresa = pctPositivo(prestadorPorId(prestadores, m.company_id)?.percentual_servico)
  if (daEmpresa) return { pct: daEmpresa, origem: 'empresa' }
  return { pct: null, origem: null }
}

// De onde veio o % de uma nota que já saiu (o servidor grava no retrato da nota;
// as de antes de 29/09 não têm → null).
export function origemDaEmissao(e: Pick<Emissao, 'snapshot'> | null | undefined): OrigemPct | null {
  const o = e?.snapshot?.servico?.percentual_origem
  return o === 'item' || o === 'nota_fixa' || o === 'empresa' ? o : null
}

// "0,5%" · "0,5% (da empresa)". Só a da empresa ganha o aviso: a da nota fixa
// e a digitada são o normal.
export function fmtPctOrigem(p: string | number | null | undefined, origem?: OrigemPct | null): string {
  const t = fmtPct(p)
  return origem === 'empresa' && t !== '—' ? `${t} (da empresa)` : t
}
