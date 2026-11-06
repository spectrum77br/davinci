// Pós-venda › Garantias (07/10/2026): Painel de Garantia Uranyx.
// Documento "Painel de Garantia — Uranyx (DaVinci)". As REGRAS moram no
// backend (apps/api/app/services/garantia.py, com os pontos do §8 decididos
// pelo dono num lugar só); aqui ficam os tipos do contrato
// (apps/api/app/schemas/garantia.py), os rótulos e as contas que a TELA faz
// só para mostrar antes de salvar (máscara do CPF, prévia da cobertura) — o
// que vale é sempre o que o servidor devolve.
// Garantia × SAC (06/11/2026, "Painel de Garantia: alterações" v3): prazo
// ÚNICO por produto (Tabela de Preços › Produtos, `garantia_meses`) — o painel
// não fala mais de hardware nem de software (a IA do Atendimento continua com
// os dois prazos, no servidor); o Atendimento é só o SAC do site CONFIRMADO
// (o botão Confirmar da aba Garantia) até alguém encerrar ou o caso terminar.

// ─── contrato da API (/api/garantias) ───────────────────────────────────────
// O status do PAINEL (06/11/2026): pelo fim único da garantia. `sem_garantia`
// (decisão do dono, 06/11/2026: "se for avulso não tem garantia") = o prazo 0
// na Tabela de Preços — vale antes dos outros (com ou sem entrega).
export type StatusGarantia = 'aguardando_entrega' | 'ativa' | 'expirada' | 'sem_prazo' | 'sem_garantia'
export type FiltroStatus = 'todos' | StatusGarantia | 'entregue_sem_data' | 'vence_30d'
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
  // Garantia automática (28/10/2026): 'robo' = criada sozinha pela NF de
  // produto; a conversa ligada mais recente (a prévia só para quem lê o
  // Atendimento).
  origem?: 'manual' | 'robo'
  conversas?: number
  ultima_mensagem_em?: string | null
  ultima_previa?: string | null
  ultima_autor?: string | null
  ultima_plataforma?: string | null
  // Pedido de garantia (28/10/2026): o Painel mostra só quem PEDIU; a venda
  // sem pedido aparece na busca e em "Mostrar: todas as vendas".
  pedido_em?: string | null
  pedido_origem?: OrigemPedido | null
  pedido_origem_rotulo?: string | null
  nao_e_pedido?: boolean
  // A frase do pedido: a mensagem do cliente (só para quem lê o Atendimento)
  // ou o motivo da devolução por defeito.
  pedido_trecho?: string | null
  // O aparelho na tela Devoluções (30/10/2026): o selo ("No técnico · faltam
  // 5 dias", "Consertado (Novo)", "Perdido (Extraviado)"); o gasto em reais só
  // vem para admin.
  devolucao_situacao?: DesfechoAparelho | null
  devolucao_rotulo?: string | null
  // No técnico com o prazo de 30 dias vencido (o dia em São Paulo).
  devolucao_vencido?: boolean
  devolucao_gasto?: number | null
  // As 3 partes do painel (04/11/2026): o departamento da linha do produto e,
  // para quem está em atendimento agora, o motivo (o selo da aba Atendimento).
  departamento?: Departamento | null
  atendimento?: AtendimentoAgora | null
  // Garantia × SAC (06/11/2026): o nome do comprador sem o apelido do fim
  // ("Maria Souza (maria123)" → "Maria Souza"); o produto como está na Tabela
  // de Preços pelo SKU (fora dela, o nome da nota, com o aviso); o prazo
  // único do produto e o fim; a situação do pedido no Bling; e o caso do SAC
  // do site — o mesmo que a aba Atendimento usa (revisão 06/11): o confirmado
  // e aberto mais antigo; senão o aberto sem confirmação mais antigo; senão o
  // encerrado mais recente.
  nome?: string
  produto_nome?: string | null
  produto_na_tabela?: boolean
  garantia_meses?: number | null
  fim_garantia?: string | null
  status_pedido?: string | null
  sac?: SacDaLinha | null
}
export type DesfechoAparelho = 'no_tecnico' | 'consertado' | 'perdido' | 'devolvido' | 'reembolso' | 'a_caminho'

// "Em atendimento" (04/11/2026, services/garantia_atendimento.py): quem pediu
// garantia e ainda tem algo em aberto — o 1º motivo que vale, nesta ordem. O
// rótulo vem pronto do servidor; `desde` = quando o motivo começou.
export type MotivoAtendimento = 'no_tecnico' | 'sem_condicao' | 'voltando' | 'plataforma_decidindo' | 'pedido_sem_resposta'
export type AtendimentoAgora = { motivo: MotivoAtendimento; rotulo: string; desde: string | null; vencido: boolean }

// O caso do SAC do site na linha (06/11/2026): recebido (protocolo, data e o
// botão Confirmar) ou confirmado ("Garantia acionada", vai para a aba
// Atendimento até encerrar).
export type SacDaLinha = {
  id: number
  protocolo: string
  recebido_em: string
  confirmado_em: string | null
  confirmado_por: PessoaRef | null
  encerrado_em: string | null
  encerrado_motivo: string | null
}
// A resposta do Confirmar, do Encerrar e do vincular (SacOut): o caso como
// ficou — a tela atualiza a linha com ela (revisão 06/11: depois do Encerrar o
// painel continuava com o caso aberto até alguém trocar de garantia).
export type SacOut = SacDaLinha & {
  garantia_id: number | null
  conversa_id: string
  vinculo: string | null
  encerrado_por: PessoaRef | null
}
/** Só os campos da linha (SacDaLinha) de um caso que veio do servidor. */
export function sacDaLinha(s: SacDaLinha): SacDaLinha {
  return {
    id: s.id,
    protocolo: s.protocolo,
    recebido_em: s.recebido_em,
    confirmado_em: s.confirmado_em ?? null,
    confirmado_por: s.confirmado_por ?? null,
    encerrado_em: s.encerrado_em ?? null,
    encerrado_motivo: s.encerrado_motivo ?? null,
  }
}
export type SituacaoDoSac = 'sem' | 'recebido' | 'acionada'
export function situacaoDoSac(s: SacDaLinha | null | undefined): SituacaoDoSac {
  if (!s) return 'sem'
  return s.confirmado_em ? 'acionada' : 'recebido'
}

// O resumo do SAC (GET /api/garantias/{id}/sac): o formulário do site lido em
// seções (Atendimento, Comprador, Compra, Produto, Defeito) e os anexos do
// e-mail (Documentação), que abrem no próprio painel. O CPF já vem mascarado
// do servidor para quem não tem "Ver o CPF completo".
export type SacCampo = { rotulo: string; valor: string | null }
export type SacSecao = { titulo: string; campos: SacCampo[] }
export type SacAnexo = { id: string; nome: string | null; tipo: string | null; tamanho: number | null; papel: string | null; url: string }
export type SacDetalhe = {
  id: number
  protocolo: string
  recebido_em: string
  conversa_id: string
  confirmado_em: string | null
  confirmado_por: PessoaRef | null
  encerrado_em: string | null
  encerrado_por: PessoaRef | null
  encerrado_motivo: string | null
  secoes: SacSecao[]
  anexos: SacAnexo[]
}

// "SAC sem pedido" (GET /api/garantias/sac/sem-pedido): o formulário que o
// robô não ligou a nenhuma venda (nº de pedido, NF e CPF + produto não
// acharam exatamente uma). Sem CPF. Só quem enxerga todas as lojas vê.
export type SacSemPedido = {
  id: number
  protocolo: string
  recebido_em: string
  nome: string | null
  pedido_citado: string | null
  nota_fiscal: string | null
  modelo: string | null
  local_compra: string | null
  loja_vendedor: string | null
}
export type SacSemPedidoLista = { itens: SacSemPedido[]; total: number }

/** O nome sem o apelido do fim (a mesma regra do servidor): "Maria Souza (maria123)" e o cortado "Maria Souza (maria12" → "Maria Souza". */
export function nomeSemApelido(nome: string | null | undefined): string {
  const original = nome || ''
  const sem = original.replace(/\s*\([^()]*\)?\s*$/, '')
  return sem.trim() ? sem : original
}
/** O nome da linha: o que o servidor manda (`nome`); sem ele, o da NF sem o apelido. */
export function nomeDaLinha(g: Pick<GarantiaLinha, 'cliente_nome' | 'nome'>): string {
  return g.nome || nomeSemApelido(g.cliente_nome)
}

/** "Aberto há 12 dias" — do formulário até o encerramento (ou hoje, o dia em São Paulo). */
export function tempoEmAberto(s: Pick<SacDaLinha, 'recebido_em' | 'encerrado_em'> | null | undefined, hoje: string = hojeSP()): string {
  if (!s) return ''
  const ini = diaEmSP(s.recebido_em)
  const fim = diaEmSP(s.encerrado_em) || hoje
  if (!ini) return ''
  const dias = Math.round((Date.parse(`${fim}T00:00:00Z`) - Date.parse(`${ini}T00:00:00Z`)) / 86_400_000)
  if (!Number.isFinite(dias)) return ''
  const n = Math.max(0, dias)
  if (s.encerrado_em) return n === 1 ? 'Ficou aberto 1 dia' : n === 0 ? 'Encerrado no mesmo dia' : `Ficou aberto ${n} dias`
  if (n === 0) return 'Aberto hoje'
  return n === 1 ? 'Aberto há 1 dia' : `Aberto há ${n} dias`
}

export type OrigemPedido = 'mensagem' | 'sac' | 'devolucao' | 'atendimento' | 'manual' | 'pessoa'
// Sem emoji (29/10/2026: "igual aos nossos painéis do DaVinci").
export const ORIGEM_PEDIDO: Record<OrigemPedido, { rotulo: string }> = {
  mensagem: { rotulo: 'mensagem do cliente' },
  sac: { rotulo: 'SAC do site' },
  devolucao: { rotulo: 'devolução por defeito' },
  atendimento: { rotulo: 'atendimento registrado' },
  manual: { rotulo: 'cadastrada à mão' },
  pessoa: { rotulo: 'marcada pela equipe' },
}
const maiuscula = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
export function rotuloDoPedido(g: Pick<GarantiaLinha, 'pedido_em' | 'pedido_origem' | 'nao_e_pedido'>): string {
  if (g.nao_e_pedido) return 'não é pedido de garantia'
  if (!g.pedido_em || !g.pedido_origem) return 'sem pedido de garantia'
  const o = ORIGEM_PEDIDO[g.pedido_origem]
  return `${maiuscula(o ? o.rotulo : g.pedido_origem)} · ${dataBR(g.pedido_em)}`
}

// Na lista: a frase do pedido quando há ("“o celular parou de funcionar” ·
// 27/10/2026" ou "Devolução: Danificado (Outros) · 27/10/2026"); sem ela, de
// onde veio e o dia.
export function rotuloDoPedidoComTrecho(g: Pick<GarantiaLinha, 'pedido_em' | 'pedido_origem' | 'nao_e_pedido' | 'pedido_trecho'>): string {
  if (!g.pedido_trecho || g.nao_e_pedido || !g.pedido_em || !g.pedido_origem) return rotuloDoPedido(g)
  const frase = g.pedido_origem === 'mensagem' ? `“${g.pedido_trecho}”` : g.pedido_trecho
  return `${maiuscula(frase)} · ${dataBR(g.pedido_em)}`
}

// Os cartões da aba Garantia e a linha embaixo deles: do departamento da
// pílula (04/11/2026), sem os filtros da barra.
// Desde 06/11/2026 a base são TODAS as vendas que a pessoa enxerga na pílula
// (`total`); `acionadas` = com o SAC confirmado; `atendimentos_no_mes` = casos
// do SAC confirmados no mês.
export type Indicadores = {
  ativas: number
  vence_30d: number
  sem_prazo: number
  atendimentos_no_mes: number
  // O prazo 0 (vendido sem garantia): não é cartão, só a linha embaixo deles.
  sem_garantia: number
  acionadas: number
  aguardando_entrega: number
  entregue_sem_data: number
  expiradas: number
  total: number
  // As vendas com garantia que a pessoa enxerga.
  vendidas?: number
}

// As 3 partes do painel (04/11/2026): o que a pessoa enxerga em cada
// departamento (a equipe dela), sem os filtros da barra — como os cartões. As
// pílulas e as abas mostram estes números.
export type ContagemDaAba = { garantias: number; atendimento: number }
export type GarantiaLista = {
  itens: GarantiaLinha[]
  total: number
  indicadores: Indicadores
  hoje: string
  abas?: Record<Departamento, ContagemDaAba> | null
  // A busca não achou garantia, mas achou o pedido (dentro da equipe): o
  // cartão diz por que ele ainda não tem garantia.
  pedido_sem_garantia?: PedidoSemGarantia | null
  // Quantos SAC do site o robô não ligou a nenhuma venda (só para quem
  // enxerga todas as lojas: o SAC não tem loja).
  sac_sem_pedido?: number
}

// Por que o pedido ainda não tem garantia (04/11/2026): o SAC busca o nº da
// plataforma de um pedido que o robô não pegou. Nunca traz CPF nem nome.
export type MotivoSemGarantia = 'antes_das_notas' | 'cancelado' | 'pendencia' | 'sem_nf_produto' | 'sem_produto_uranyx' | 'outro'
export type PedidoSemGarantia = {
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  data: string | null
  situacao: string | null
  produto_uranyx: boolean
  motivo: MotivoSemGarantia
  motivo_texto: string
  pode_cadastrar: boolean
}

/** "Pedido 288113 · Mercado Livre Loja X · nº na plataforma 2000… · compra 30/07/2026 · Entregue — ainda sem garantia: <motivo>". */
export function textoPedidoSemGarantia(p: PedidoSemGarantia): string {
  const loja = [nomePlataforma(p.plataforma), p.conta].filter(Boolean).join(' ')
  const partes = [`Pedido ${p.pedido_bling}`]
  if (loja) partes.push(loja)
  if (p.pedido_marketplace) partes.push(`nº na plataforma ${p.pedido_marketplace}`)
  if (p.data) partes.push(`compra ${dataBR(p.data)}`)
  if (p.situacao) partes.push(p.situacao)
  return `${partes.join(' · ')} — ainda sem garantia: ${p.motivo_texto}`
}

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
  prazos: { data_inicio: string | null; fim_hardware: string | null; fim_software: string | null; status: StatusGarantia; status_rotulo: string; entregue_sem_data: boolean; fim_garantia?: string | null; prazo_meses?: number | null }
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
// Status sem emoji (29/10/2026): a cor fica no selo (GarantiaStatus).
export const STATUS: Record<StatusGarantia, { rotulo: string; dica: string }> = {
  aguardando_entrega: {
    rotulo: 'Aguardando entrega',
    dica: 'cadastrada, mas o pedido ainda não tem data de entrega — os prazos entram sozinhos quando ela aparecer',
  },
  ativa: {
    rotulo: 'Ativa',
    dica: 'hoje está dentro do prazo da garantia (entrega + o prazo do produto)',
  },
  expirada: {
    rotulo: 'Expirada',
    dica: 'o prazo da garantia acabou',
  },
  sem_prazo: {
    rotulo: 'Prazo não cadastrado',
    dica: 'o produto não tem o prazo da garantia na Tabela de Preços › Produtos (coluna Garantia, em meses) — sem ele não há data final',
  },
  sem_garantia: {
    rotulo: 'Sem garantia',
    dica: 'o produto foi vendido sem garantia (prazo 0 na Tabela de Preços › Produtos, como o avulso)',
  },
}
// O "Aguardando entrega" de pedido que o Bling já dá como entregue (o
// DaVinci não tem a data: a Logística só guarda a data desde 15/07/2026).
// Mesmo status (sem data não há prazo — RN01), outro texto: não sugerir que
// o produto ainda não chegou.
export const ENTREGUE_SEM_DATA = {
  rotulo: 'Entregue — sem data no DaVinci',
  dica: 'o Bling já marca o pedido como entregue, mas nenhuma fonte do DaVinci tem a data de entrega — sem ela não há prazo (a data inicial não se digita)',
}
export function statusInfo(s: string | null | undefined, entregueSemData = false) {
  if (s === 'aguardando_entrega' && entregueSemData) return ENTREGUE_SEM_DATA
  return STATUS[(s || '') as StatusGarantia] ?? { rotulo: s || '—', dica: '' }
}

export const FILTROS_STATUS: { value: FiltroStatus; label: string }[] = [
  { value: 'todos', label: 'Todos' },
  { value: 'ativa', label: 'Ativa' },
  { value: 'expirada', label: 'Expirada' },
  { value: 'aguardando_entrega', label: 'Aguardando entrega' },
  { value: 'entregue_sem_data', label: 'Entregue — sem data no DaVinci' },
  { value: 'vence_30d', label: 'Vence em 30 dias' },
  { value: 'sem_prazo', label: 'Sem prazo cadastrado' },
  { value: 'sem_garantia', label: 'Sem garantia' },
]

export const COBERTURA: Record<Cobertura, { rotulo: string }> = {
  coberto: { rotulo: 'Coberto' },
  fora_da_garantia: { rotulo: 'Fora da garantia' },
  sem_data_de_entrega: { rotulo: 'Sem data de entrega' },
}
export function coberturaInfo(c: string | null | undefined) {
  return COBERTURA[(c || '') as Cobertura] ?? { rotulo: c || '—' }
}

// O tipo do problema do "Registrar atendimento" (revisão 06/11/2026): só uma
// categoria — a cobertura é pelo fim único da garantia (o servidor só cai no
// prazo antigo do tipo quando o produto não tem prazo cadastrado). O valor
// gravado continua hardware/software (a coluna e a trava do banco não
// mudaram); a tela não mostra essas palavras.
export const TIPOS_PROBLEMA: { value: TipoProblema; label: string; dica: string }[] = [
  { value: 'hardware', label: 'Defeito no aparelho', dica: 'parte física: tela, bateria, carregamento, botão, placa…' },
  { value: 'software', label: 'Sistema / aplicativo', dica: 'sistema, app, atualização, configuração…' },
]
export function rotuloDoTipo(t: string | null | undefined): string {
  return TIPOS_PROBLEMA.find((x) => x.value === t)?.label || t || '—'
}

// Avisos que não bloqueiam (GET /pedido e POST/PUT).
export const AVISOS: Record<string, string> = {
  pedido_sem_produto_uranyx: 'O pedido não tem produto Uranyx (SKU u…, dg… ou a0…). Confira se é o pedido certo.',
  aguardando_entrega: 'O pedido ainda não tem data de entrega: a garantia fica "Aguardando entrega" e o prazo entra sozinho quando a entrega aparecer.',
  entregue_sem_data: 'O Bling já dá o pedido como entregue, mas o DaVinci não tem a data de entrega (a Logística só guarda a data desde 15/07/2026). A garantia fica sem prazo até a data aparecer — a data inicial não se digita.',
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
  criou_automatico: 'Criou sozinho pela NF de produto',
  descartou: 'Descartou (pedido cancelado)',
  reativou: 'Reativou (pedido saiu de Cancelado)',
  pedido_automatico: 'Pedido de garantia (achado pelo robô)',
  marcou_pedido: 'Marcou como pedido de garantia',
  tirou_pedido: 'Não é pedido de garantia',
  // Garantia × SAC (06/11/2026).
  confirmou_atendimento: 'Confirmou o atendimento (SAC do site)',
  encerrou_atendimento: 'Encerrou o atendimento',
  vinculou_sac: 'Ligou o SAC do site a esta garantia',
}
export const CAMPOS_LOG: Record<string, string> = {
  pedido: 'Pedido',
  // Reabrir atendimento (06/11/2026): encerrado → reaberto.
  atendimento: 'Atendimento',
  nf_numero: 'NF',
  nf_serie: 'Série da NF',
  cpf: 'CPF',
  cliente_nome: 'Nome do cliente',
  data_inicio: 'Data inicial (entrega)',
  // O log antigo (antes do prazo único de 06/11/2026) continua com os dois.
  fim_hardware: 'Fim do prazo antigo (3 meses)',
  fim_software: 'Fim do prazo antigo (12 meses)',
  prazo_meses: 'Prazo da garantia (meses)',
  fim_garantia: 'Fim da garantia',
  // O Confirmar com a garantia já vencida (o "Confirmar mesmo assim?"): o fim
  // que a tela avisou.
  garantia_vencida: 'Confirmou com a garantia já vencida (fim)',
  // O Confirmar do produto vendido sem garantia (prazo 0, 06/11/2026).
  sem_garantia: 'Confirmou com o produto vendido sem garantia (prazo)',
  entrega_origem: 'Origem da entrega',
  // O pedido de garantia que o robô tinha achado, desfeito ao corrigir o pedido.
  pedido_de_garantia: 'Pedido de garantia',
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
  so_admin: 'O Resumo da garantia é só para administradores.',
  // Garantia × SAC (06/11/2026).
  sac_nao_encontrado: 'Caso do SAC não encontrado (ou de uma loja fora da sua equipe).',
  sac_sem_garantia: 'Este SAC ainda não está ligado a uma venda — vincule em "SAC sem pedido" antes de confirmar.',
  garantia_vencida: 'A garantia desta venda já venceu.',
  produto_sem_garantia: 'Este produto foi vendido sem garantia.',
  sac_nao_confirmado: 'O atendimento ainda não foi confirmado — confirme na aba Garantia antes de encerrar.',
  sac_ja_confirmado: 'Este SAC já foi confirmado em outra garantia — não dá para mudar o vínculo.',
  sac_restrito: 'Só quem enxerga todas as lojas vincula o SAC do site à mão.',
  sac_outra_pessoa: 'Outra pessoa escreveu na conversa deste SAC (ou ela junta e-mails de mais de um cliente): o DaVinci não liga à venda, para não mostrar os dados de um cliente na garantia de outro. Confira a conversa no Atendimento.',
  anexo_restrito: 'Sem acesso a este arquivo.',
  forbidden: 'Sem permissão para isso.',
}

const MSG_PYDANTIC: [RegExp, string][] = [
  [/extra (inputs|fields) (are )?not permitted/i, 'Este campo é calculado pelo sistema e não pode ser enviado.'],
  [/at least 1 character/i, 'Campo obrigatório.'],
  [/at least 3 characters/i, 'Mínimo de 3 caracteres.'],
  [/at least 11 characters/i, 'CPF incompleto.'],
  [/at most (\d+) characters/i, 'Texto longo demais.'],
  [/field required/i, 'Campo obrigatório.'],
  [/input should be 'hardware' or 'software'/i, 'Escolha o tipo do problema.'],
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
  // A aba Resumo do painel (a garantia por produto, com saldo e gasto em
  // reais): só admin (04/11/2026; o POST /resumo também recusa os outros).
  resumo: boolean
}

export function acessoGarantia(u: UsuarioLike): AcessoGarantia {
  if (!u) return { consulta: false, cadastra: false, registra: false, veCpf: false, ve: false, log: false, resumo: false }
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
    resumo: admin,
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

/**
 * §5.2 antes de salvar: Coberto se o dia do atendimento ≤ fim da garantia —
 * a mesma conta do servidor (services/garantia.py `cobertura_em`, revisão
 * 06/11/2026): com o prazo do produto, o fim ÚNICO (`fim_garantia`), qualquer
 * que seja o tipo; sem prazo cadastrado, o fim antigo do tipo. Sem o tipo e
 * sem o prazo não dá para dizer: null. Produto vendido sem garantia (prazo 0):
 * fora da garantia, sem fim.
 */
export function previaCobertura(
  g: Pick<GarantiaLinha, 'data_inicio' | 'fim_hardware' | 'fim_software' | 'fim_garantia' | 'garantia_meses'>,
  tipo: TipoProblema | null,
  quando: string | null,
  ultimoDiaCoberto = true,
): { cobertura: Cobertura; fim: string | null; dia: string | null } | null {
  if (vendidoSemGarantia(g)) return { cobertura: 'fora_da_garantia', fim: null, dia: diaEmSP(quando) || hojeSP() }
  if (!g.data_inicio) return { cobertura: 'sem_data_de_entrega', fim: null, dia: diaEmSP(quando) }
  const fim = g.fim_garantia || (tipo === 'hardware' ? g.fim_hardware : tipo === 'software' ? g.fim_software : undefined)
  if (fim === undefined) return null
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

const _dias = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000)
/**
 * A barra do prazo único (06/11/2026), a mesma conta do servidor
 * (services/garantia.prazo_info): do início (a entrega) ao fim da garantia.
 * Sem entrega ou sem prazo do produto, nada (null).
 */
export function prazoUnico(g: Pick<GarantiaLinha, 'data_inicio' | 'fim_garantia'>, hoje: string = hojeSP(), ultimoDiaCoberto = true): Prazo | null {
  if (!g.data_inicio || !g.fim_garantia) return null
  const total = _dias(g.data_inicio, g.fim_garantia)
  const restantes = Math.max(0, Math.min(_dias(hoje, g.fim_garantia), total))
  return { fim: g.fim_garantia, dias_total: total, dias_restantes: restantes, coberto_hoje: coberto(g.fim_garantia, hoje, ultimoDiaCoberto) }
}
/** "12 meses" / "1 mês" / "sem garantia" (0) / "—" (sem prazo cadastrado). */
/**
 * A coluna Garantia em DIAS (06/11/2026, Eduardo: "e a garantia deixe em
 * dias"): os dias que faltam até a Garantia total, contados do `hoje` do
 * servidor (São Paulo), como a Validade do certificado na Tabela de Preços.
 * Sem entrega ainda, "aguarda entrega" (o prazo só começa na entrega); o
 * prazo do produto vai no balão. Sem garantia (0) e sem prazo ficam com o
 * texto deles, fora daqui. PURA.
 */
export function diasDaGarantia(
  g: { fim_garantia?: string | null; garantia_meses?: number | null },
  hoje: string | null | undefined,
): { texto: string; tom: 'normal' | 'perto' | 'vencida' | 'espera'; titulo: string } {
  const prazo = textoMeses(g.garantia_meses)
  if (!g.fim_garantia || !hoje) {
    return { texto: 'aguarda entrega', tom: 'espera', titulo: `prazo do produto: ${prazo} — começa a contar na entrega` }
  }
  const dia = (v: string) => Date.UTC(Number(v.slice(0, 4)), Number(v.slice(5, 7)) - 1, Number(v.slice(8, 10)))
  const n = Math.round((dia(g.fim_garantia) - dia(hoje)) / 86_400_000)
  const titulo = `prazo do produto: ${prazo} · até ${dataCurta(g.fim_garantia)}`
  if (n < 0) return { texto: `vencida há ${-n} ${-n === 1 ? 'dia' : 'dias'}`, tom: 'vencida', titulo }
  if (n === 0) return { texto: 'vence hoje', tom: 'perto', titulo }
  return { texto: `${n} ${n === 1 ? 'dia' : 'dias'}`, tom: n <= 30 ? 'perto' : 'normal', titulo }
}
export function textoMeses(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n === 0) return 'sem garantia'
  return n === 1 ? '1 mês' : `${n} meses`
}
/**
 * O produto vendido sem garantia (06/11/2026, "se for avulso não tem
 * garantia"): o prazo 0 na Tabela de Preços — diferente do vazio (null, "prazo
 * não cadastrado").
 */
export function vendidoSemGarantia(g: { garantia_meses?: number | null } | null | undefined): boolean {
  return g?.garantia_meses === 0
}

// ─── lista ──────────────────────────────────────────────────────────────────
export type FiltrosLista = {
  busca: string
  status: FiltroStatus
  // `pedido`: a data do pedido de garantia (28/10/2026). `venda` (o padrão
  // desde 05/11/2026, "se eu colocar uma data, filtra o saldo daqueles dias"):
  // o dia da compra no Bling — as garantias das vendas feitas no período.
  periodo: 'cadastro' | 'entrega' | 'pedido' | 'venda'
  de: string
  ate: string
  com_atendimento_no_mes: boolean
  // Garantia automática (28/10/2026): a vista "Conversas" ordena pela última
  // mensagem e pode mostrar só as garantias com conversa ligada.
  ordem?: 'cadastro' | 'atividade'
  com_conversa?: boolean
  origem?: 'todas' | 'robo' | 'manual'
  // O "Mostrar" (06/11/2026): todas as vendas com nota (o padrão) ou só as
  // de garantia acionada (o SAC confirmado, encerrado ou não).
  mostrar?: 'todas' | 'acionada'
  // A garantia por produto: o número de uma coluna abre só os pedidos dela.
  ids?: number[]
  // As 3 partes do painel (04/11/2026): o departamento da pílula; a linha do
  // Resumo (a `chave` do produto: todas as garantias dele); e a aba
  // Atendimento (desde 06/11/2026: só os casos do SAC confirmados e abertos,
  // o mais antigo em cima).
  departamento?: Departamento | null
  produto?: string | null
  em_atendimento?: boolean
}

export const FILTROS_PADRAO: FiltrosLista = { busca: '', status: 'todos', periodo: 'venda', de: '', ate: '', com_atendimento_no_mes: false, mostrar: 'todas' }

export const LIMITE_PAGINA = 100

export type CorpoLista = {
  limite: number
  offset: number
  busca?: string
  status?: FiltroStatus
  periodo?: 'cadastro' | 'entrega' | 'pedido' | 'venda'
  de?: string
  ate?: string
  com_atendimento_no_mes?: boolean
  ordem?: 'cadastro' | 'atividade'
  com_conversa?: boolean
  origem?: 'robo' | 'manual'
  mostrar?: 'todas' | 'acionada'
  ids?: number[]
  departamento?: Departamento
  produto?: string
  em_atendimento?: boolean
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
  if (f.ordem === 'atividade') q.ordem = 'atividade'
  // "Só com conversa" não vale para a BUSCA: quem procura o CPF/NF/pedido
  // quer achar a garantia mesmo sem conversa nenhuma.
  if (f.com_conversa && !busca) q.com_conversa = true
  if (f.origem && f.origem !== 'todas') q.origem = f.origem
  // "todas" é o padrão do servidor: só a "garantia acionada" vai no corpo.
  if (f.mostrar === 'acionada') q.mostrar = 'acionada'
  if (f.ids?.length) q.ids = f.ids
  if (f.departamento) q.departamento = f.departamento
  if (f.produto) q.produto = f.produto
  if (f.em_atendimento) q.em_atendimento = true
  return q
}

// ─── as 3 partes do painel (04/11/2026) ─────────────────────────────────────
// "vamos dividir o painel garantia em 3 partes" (Eduardo): Resumo (a visão por
// produto, só admin), Garantia (todas as garantias) e Atendimento (só o que
// está em atendimento agora) — no estilo da Tabela de Preços: as pílulas de
// departamento em cima e as abas embaixo. Os departamentos são as raízes da
// Tabela de Preços (services/garantia_resumo.py).
export type Departamento = 'celular' | 'mala' | 'eletro'
export const DEPARTAMENTOS: { chave: Departamento; nome: string }[] = [
  { chave: 'celular', nome: 'Celular' },
  { chave: 'mala', nome: 'Mala' },
  { chave: 'eletro', nome: 'Eletro' },
]
export type Aba = 'resumo' | 'garantia' | 'atendimento'

// A URL guarda a aba (?aba=) e o departamento (?dept=). O admin abre no
// Resumo; os outros, na Garantia — e quem não é admin nunca cai no Resumo.
const primeiro = (v: unknown) => (Array.isArray(v) ? v[0] : v)
export function abaDaUrl(v: unknown, admin: boolean): Aba {
  const a = primeiro(v)
  if (a === 'garantia' || a === 'atendimento') return a
  return admin ? 'resumo' : 'garantia'
}
export function departamentoDaUrl(v: unknown): Departamento {
  const d = primeiro(v)
  return DEPARTAMENTOS.find((x) => x.chave === d)?.chave ?? 'celular'
}

/** "Mercado Livre · Loja X" — a coluna Loja da Tabela. */
export function lojaDaGarantia(g: Pick<GarantiaLinha, 'plataforma' | 'conta'>): string {
  return [nomePlataforma(g.plataforma), g.conta].filter(Boolean).join(' · ') || '—'
}

/** Há quanto tempo (o dia em São Paulo): "hoje", "há 1 dia", "há 12 dias". */
export function haQuantoTempo(desde: string | null | undefined, hoje: string = hojeSP()): string {
  const dia = diaEmSP(desde)
  if (!dia) return ''
  const dias = Math.round((Date.parse(`${hoje}T00:00:00Z`) - Date.parse(`${dia}T00:00:00Z`)) / 86_400_000)
  if (!Number.isFinite(dias)) return ''
  if (dias <= 0) return 'hoje'
  return dias === 1 ? 'há 1 dia' : `há ${dias} dias`
}

/** O selo da aba Atendimento: "No técnico · há 42 dias · prazo vencido" — o
 *  tempo é desde quando o motivo começou (o aparelho foi para o técnico); o
 *  "vencido" vai no fim, sem número, para não confundir com o "vencido há N
 *  dias" do aparelho (que conta desde o fim do prazo). */
export function seloDoAtendimento(a: AtendimentoAgora, hoje?: string): string {
  const base = a.vencido ? a.rotulo.replace(/ · vencido$/, '') : a.rotulo
  return [base, haQuantoTempo(a.desde, hoje), a.vencido ? 'prazo vencido' : ''].filter(Boolean).join(' · ')
}

// ─── resumo por produto (28/10/2026; 3 partes desde 04/11/2026) ─────────────
// POST /api/garantias/resumo (só admin): a garantia POR PRODUTO — uma linha
// por produto da Tabela de Preços (o modelo, com as cores juntas), com as
// vendas e o SALDO delas (a planilha de rentabilidade: base − comissão − frete
// do Bling), como os pedidos de garantia chegaram (mensagem, SAC, devolução)
// e a manutenção (consertos, perdidos e o GASTO, da tela Reembolso); e o total
// por departamento. A % é gasto ÷ saldo × 100 (04/11/2026: "% --> manutenção
// gastos/saldo *100"), feita na tela, de um jeito só (pctDoGasto).
export type ResumoDepartamento = {
  chave: Departamento
  nome: string
  vendidas: number
  pediram: number
  // Em reais, só para admin (como os do produto).
  saldo?: number | null
  gasto?: number | null
}
// As colunas do Resumo que abrem só os pedidos delas.
export type ColunaDePedidos = 'mensagem' | 'sac' | 'devolucao' | 'consertos' | 'perdidos' | 'gasto'
export type ResumoProduto = {
  chave: string
  departamento: Departamento
  nome: string
  skus: string[]
  // false = vendido, mas sem linha na Tabela de Preços (entra pelo SKU).
  na_tabela: boolean
  vendidas: number
  pediram: number
  seguiram: number
  // Como pediu (a coluna Atendimento): mensagem, SAC do site, devolução por defeito.
  origens: Partial<Record<'mensagem' | 'sac' | 'devolucao', number>>
  // Manutenção (a tela Devoluções): dos que pediram, os consertados e os
  // perdidos (Extraviado/Sucata). Uma garantia conta numa só.
  consertos: number
  perdidos: number
  // Em reais, só para admin: o gasto (tela Reembolso) de quem pediu garantia
  // = conserto + perdidos + reembolso ao cliente; o saldo de TODAS as vendas.
  gasto?: number | null
  gasto_conserto?: number | null
  gasto_perdidos?: number | null
  gasto_cliente?: number | null
  saldo?: number | null
  // As garantias com pedido; e os mesmos por coluna da tela (a célula abre só
  // os dela). Coluna sem pedido não vem.
  pedidos: number[]
  pedidos_por_coluna?: Partial<Record<ColunaDePedidos, number[]>>
}
export type Resumo = { vendidas: number; pediram: number; seguiram: number; hoje: string; ve_valores: boolean; departamentos: ResumoDepartamento[]; produtos: ResumoProduto[] }

// O corpo do resumo: os filtros que a aba Resumo mostra (o período; a origem
// se vier). O Status saiu do Resumo em 06/11/2026 ("excluir o filtro
// Status"): um status ligado na aba Garantia não vai para o /resumo. A busca,
// o "Mostrar" e os cartões são da aba Garantia.
export function corpoDoResumo(f: FiltrosLista): Partial<CorpoLista> {
  const q: Partial<CorpoLista> = {}
  if (f.de || f.ate) {
    q.periodo = f.periodo
    if (f.de) q.de = f.de
    if (f.ate) q.ate = f.ate
  }
  if (f.origem && f.origem !== 'todas') q.origem = f.origem
  return q
}

// A % do Resumo: gasto ÷ saldo × 100, com 2 casas ("3,45%"); sem saldo, "—".
export function pctDoGasto(gasto: number | null | undefined, saldo: number | null | undefined): string {
  if (!saldo) return '—'
  const v = ((gasto || 0) / saldo) * 100 || 0
  return `${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
}
// Largura da barra (0–100), com um mínimo visível para o que não é zero.
export function largura(parte: number, total: number): number {
  if (!total || !parte) return 0
  return Math.max(1.5, Math.min(100, (parte / total) * 100))
}
export function numeroBR(n: number): string {
  return (n || 0).toLocaleString('pt-BR')
}
// R$ 1.234,50 (a dica e o rodapé); na célula estreita vai sem centavos.
export function reaisBR(v: number | null | undefined): string {
  return `R$ ${(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

// ─── histórico das conversas (garantia automática, 28/10/2026) ──────────────
// GET /api/garantias/{id}/historico: as conversas ligadas à garantia, lidas
// na hora do Atendimento (sem cópia), e os marcos (compra, entrega, outro
// pedido do mesmo comprador, atendimento registrado).
export type HistoricoConversa = {
  id: string
  plataforma: string
  canal: string
  comprador_nome: string | null
  conta: string | null
  pedido_marketplace: string | null
  como: 'pedido' | 'comprador'
  link: string
  email: boolean
  // SAC do site (28/10/2026): o protocolo do formulário (US-26-NNNN).
  protocolo?: string | null
}
export type HistoricoMensagem = {
  id: string
  conversa_id: string
  autor: string
  origem: string
  tipo: string
  texto: string | null
  anexos: Record<string, unknown>[]
  enviada_em: string | null
  status: string | null
  // SAC do site: é o e-mail do formulário (o servidor decide pelo conteúdo).
  formulario_sac?: boolean
  // É a mensagem em que o cliente pediu a garantia (fica destacada).
  pedido_garantia?: boolean
}
export type HistoricoMarco = {
  tipo: 'compra' | 'entrega' | 'outro_pedido' | 'atendimento'
  em: string
  texto: string
  atendimento_id: number | null
  // A data da compra (Bling) e a entrega sem instante: sem hora na tela.
  sem_hora?: boolean
}
export type Historico = {
  garantia_id: number
  restrito: boolean
  conversas: HistoricoConversa[]
  mensagens: HistoricoMensagem[]
  marcos: HistoricoMarco[]
  desde: string | null
  cortadas: number
}

// O e-mail do formulário do SAC chega com o rótulo colado no valor
// ("CPF529.982.247-25", "Número do pedido261027QC4PXFRR"): na tela, um ":"
// entre os dois. Só na exibição — o texto gravado não muda.
const ROTULOS_SAC = [
  'Nome completo (conforme consta na compra)', 'CPF', 'E-mail', 'Telefone/WhatsApp', 'CEP', 'Endereço completo',
  'Local da compra (site, marketplace ou loja física)', 'Nome da loja/vendedor', 'Número do pedido', 'Data da compra',
  'Data de recebimento', 'Número da nota fiscal (se disponível)', 'Número da nota fiscal', 'Valor pago', 'Modelo', 'Cor',
  'Voltagem (127V ou 220V)', 'Número de série', 'Data em que o problema foi identificado', 'Descrição detalhada do defeito',
  'O produto sofreu queda, contato com líquidos ou foi utilizado em voltagem diferente da indicada?',
  'Tipo de conexão utilizada (tomada direta, extensão ou adaptador)',
].sort((a, b) => b.length - a.length)
// Cada rótulo vale uma vez e só na linha que abre parágrafo (o formulário põe
// uma linha em branco entre os campos): a continuação da descrição fica como o
// cliente escreveu ("Correios entregou…" não vira "Cor: reios…"). Na tela, um
// campo por linha, com a linha em branco só antes de cada seção ("2. Dados…").
// O CPF do formulário sai mascarado (06/11/2026: "o CPF fica mascarado, como
// no resto do painel"); o completo, para quem pode, fica no Resumo do SAC.
const RE_CPF_TEXTO = /\b(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})[-.\s]?(\d{2})\b/g
export function mascararCpfNoTexto(t: string): string {
  return t.replace(RE_CPF_TEXTO, (_x, _a, b, c) => `***.${b}.${c}-**`)
}
export function formularioSacLegivel(texto: string | null | undefined, mascarar = true): string {
  const usados = new Set<string>()
  let anterior = ''
  const linhas = (texto || '').split(/\r?\n/).map((linha, i) => {
    const abre = i === 0 || !anterior.trim()
    anterior = linha
    const r = abre ? ROTULOS_SAC.find((x) => !usados.has(x) && linha.startsWith(x)) : undefined
    if (!r) return linha
    usados.add(r)
    const resto = linha.slice(r.length)
    if (!resto || /^\s*:/.test(resto)) return mascarar && r === 'CPF' ? mascararCpfNoTexto(linha) : linha
    const valor = mascarar && r === 'CPF' ? mascararCpfNoTexto(resto.trimStart()) : resto.trimStart()
    return `${r}${r.endsWith('?') ? '' : ':'} ${valor}`
  })
  const saida: string[] = []
  for (const linha of linhas) {
    if (!linha.trim()) continue
    if (saida.length && /^\d+\.\s/.test(linha)) saida.push('')
    saida.push(linha)
  }
  return saida.join('\n')
}

// Um anexo da mensagem, do jeito que a tela desenha: foto (miniatura que
// abre grande), vídeo (toca na tela), cartão de pedido/produto, link ou só
// o nome (o ML não manda o link do arquivo).
export type PecaHistorico =
  | { chave: string; tipo: 'imagem'; url: string; nome: string }
  | { chave: string; tipo: 'video'; url: string; nome: string }
  | { chave: string; tipo: 'cartao'; nome: string; anexo: Record<string, unknown> }
  | { chave: string; tipo: 'link'; url: string; nome: string }
  | { chave: string; tipo: 'nome'; nome: string }

const RE_VIDEO = /\.(mp4|mov|m4v|webm|3gp)(\?|$)/i
const RE_IMAGEM = /\.(png|jpe?g|webp|gif|heic)(\?|$)/i
export function pecasDoHistorico(m: Pick<HistoricoMensagem, 'id' | 'anexos'>): PecaHistorico[] {
  return (m.anexos || []).filter((a) => !!a && typeof a === 'object').map((a, i): PecaHistorico => {
    const chave = `${m.id}-${i}`
    const s = (k: string) => (typeof a[k] === 'string' ? (a[k] as string) : '')
    const tipo = (s('tipo') || s('type') || s('content_type')).toLowerCase()
    if (tipo === 'pedido' || tipo === 'produto') {
      return { chave, tipo: 'cartao', nome: tipo === 'pedido' ? 'cartão do pedido' : 'cartão do produto', anexo: a }
    }
    const bruta = s('url') || s('link') || s('src') || s('video_url') || s('image_url') || s('thumb_url') || s('url_imagem')
    const url = /^https?:\/\//i.test(bruta) ? bruta : null
    const video = tipo.includes('video') || tipo.startsWith('video/') || (!!url && RE_VIDEO.test(url))
    const nome = s('nome') || s('name') || s('filename') || s('titulo') || (video ? 'vídeo' : tipo.includes('imag') ? 'foto' : tipo || 'anexo')
    if (url && video) return { chave, tipo: 'video', url, nome }
    if (url && (tipo.includes('imag') || tipo.startsWith('image') || RE_IMAGEM.test(url))) return { chave, tipo: 'imagem', url, nome }
    if (url) return { chave, tipo: 'link', url, nome }
    return { chave, tipo: 'nome', nome }
  })
}

// A linha do tempo: as mensagens e os marcos pela hora, com o separador de dia
// e, quando há mais de uma conversa, o aviso de onde muda a conversa.
export type LinhaHistorico =
  | { tipo: 'dia'; chave: string; texto: string }
  | { tipo: 'conversa'; chave: string; conversa: HistoricoConversa | null }
  | { tipo: 'marco'; chave: string; marco: HistoricoMarco }
  | { tipo: 'msg'; chave: string; m: HistoricoMensagem }

function msHist(iso: string | null | undefined): number {
  const t = iso ? new Date(iso).getTime() : Number.NaN
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
}
export function rotuloDoDia(iso: string, agoraMs = Date.now()): string {
  const d = new Date(iso)
  const hoje = new Date(agoraMs)
  const ontem = new Date(agoraMs - 86400000)
  if (d.toDateString() === hoje.toDateString()) return 'Hoje'
  if (d.toDateString() === ontem.toDateString()) return 'Ontem'
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}
export function linhasDoHistorico(h: Historico, agoraMs = Date.now()): LinhaHistorico[] {
  type Item = { t: number; iso: string | null; ordem: number; linha: LinhaHistorico }
  const itens: Item[] = [
    ...h.marcos.map((mc, i): Item => ({ t: msHist(mc.em), iso: mc.em, ordem: 0, linha: { tipo: 'marco', chave: `marco-${i}`, marco: mc } })),
    ...h.mensagens.map((m): Item => ({ t: msHist(m.enviada_em), iso: m.enviada_em, ordem: 1, linha: { tipo: 'msg', chave: m.id, m } })),
  ]
  itens.sort((a, b) => (a.t === b.t ? a.ordem - b.ordem : a.t - b.t))
  const porId = new Map(h.conversas.map((c) => [c.id, c]))
  const varias = h.conversas.length > 1
  const out: LinhaHistorico[] = []
  let dia = ''
  let conversa: string | null = null
  for (const it of itens) {
    if (it.iso && Number.isFinite(it.t)) {
      const dd = new Date(it.iso).toDateString()
      if (dd !== dia) {
        out.push({ tipo: 'dia', chave: `dia-${dd}`, texto: rotuloDoDia(it.iso, agoraMs) })
        dia = dd
      }
    }
    if (it.linha.tipo === 'msg' && varias && it.linha.m.conversa_id !== conversa) {
      conversa = it.linha.m.conversa_id
      out.push({ tipo: 'conversa', chave: `conv-${it.linha.chave}`, conversa: porId.get(conversa) ?? null })
    }
    out.push(it.linha)
  }
  return out
}

// De que lado fica o balão (como o Atendimento: cliente à esquerda, loja à
// direita, mediador à esquerda com o rótulo; nota interna e sistema no meio).
export function ladoDaMensagem(m: Pick<HistoricoMensagem, 'autor' | 'origem'>): 'cliente' | 'loja' | 'mediador' | 'nota' | 'sistema' {
  if (m.autor === 'equipe') return 'nota'
  if (m.autor === 'mediador') return 'mediador'
  if (m.autor === 'sistema' || m.origem === 'sistema') return 'sistema'
  return m.autor === 'cliente' ? 'cliente' : 'loja'
}

export const CANAL_ROTULO: Record<string, string> = {
  chat: 'chat',
  pos_venda: 'pós-venda',
  email: 'e-mail',
  reclamacao: 'reclamação',
  avaliacao: 'avaliação',
}

// Os 4 cartões (06/11/2026) — clicar filtra a lista.
export type Cartao = 'ativas' | 'vence_30d' | 'sem_prazo' | 'atendimentos_no_mes'
export const CARTOES: { chave: Cartao; rotulo: string; dica: string }[] = [
  { chave: 'ativas', rotulo: 'Ativas', dica: 'dentro do prazo da garantia hoje' },
  { chave: 'vence_30d', rotulo: 'Vence em 30 dias', dica: 'ativas cuja garantia termina nos próximos 30 dias' },
  { chave: 'sem_prazo', rotulo: 'Sem prazo cadastrado', dica: 'o produto ainda não tem o prazo na Tabela de Preços › Produtos' },
  { chave: 'atendimentos_no_mes', rotulo: 'Atendimentos no mês', dica: 'casos do SAC do site confirmados neste mês' },
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
  if (f.status === 'vence_30d' || f.status === 'sem_prazo') return f.status
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
