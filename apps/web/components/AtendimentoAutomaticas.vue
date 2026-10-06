<script lang="ts">
// Aba "Automáticas" do Atendimento (05/10/2026). Eduardo: recriar no DaVinci
// as mensagens automáticas que o Duoke manda hoje ("daí tal dia manda tal
// mensagem"), COMEÇANDO EM MODO SECO: o DaVinci registra o que mandaria, para
// quem e quando, e não envia nada; o comparador confere com o que o Duoke
// mandou de verdade. Quando bater, a troca é loja por loja: desliga no Duoke e
// põe a regra em "Enviar" aqui. Desenho em docs/atendimento-automacoes.md
// (§8.2); a API é apps/api/app/routers/atendimento_automacoes.py.
//
// A tela:
// - a faixa com as chaves do servidor (motor, envio das automáticas, envio
//   geral, resposta automática da Shopee, teto do dia);
// - uma seção por automação (Shopee, TikTok ou ML, uma plataforma por vez),
//   com o gatilho, o atraso, o horário e os totais; aberta, uma linha por
//   loja: o modo (Desligado / Simular / Enviar), quantas mandaria em 24 h e
//   no período, a % que bateu com o Duoke (a MENOR entre precisão e
//   cobertura — nas opções, a cobertura; verde a partir de 95%, o detalhe no
//   title), "só DaVinci" e "só Duoke" (filtram o registro), o último e o
//   selo da troca DA LOJA (o critério é por loja, em 7 dias; o da automação
//   só soma as lojas);
// - o editor da regra da loja: o texto (com as {lacunas} explicadas e a
//   prévia com o nome de exemplo, pelo validador do backend), o atraso, o
//   horário, as condições do catálogo e o teto;
// - o registro recente, SEM TEXTO NENHUM: loja, automação, alvo, horários,
//   estado e o que o Duoke fez. A conversa abre na Caixa;
// - a PRÉVIA de cada linha do registro ("como o cliente receberia", 05/10 à
//   noite): só quando a pessoa abre, a API monta na hora as partes EXATAS que
//   sairiam (o cartão com o nº do pedido, o texto com o usuário do comprador,
//   o cupom, a figurinha, a resposta pública) e mostra ao lado o que o Duoke
//   mandou de verdade — balões como os da conversa, DaVinci × Duoke. Nada
//   disso fica gravado (o registro continua sem texto).
//
// As automações que SÓ SIMULAM (o pedido não pago com cupom, a resposta da
// avaliação, o "pedido recebido" do TikTok — `so_simulacao`) têm o Enviar
// travado com o porquê, sempre.
//
// "Enviar" fica DESABILITADO, com o motivo, enquanto a API disser que não
// pode (a chave ATENDIMENTO_AUTOMACOES_ENVIO desligada, o envio geral, a
// campanha da Shopee sem a resposta automática, a loja sem acesso); a API
// recusa de novo (409) e pede a confirmação "desliguei no Duoke" e, se o
// critério da troca não passou NESTA loja, a de quem troca mesmo assim
// (422). Aqui ficam os
// tipos (= as saídas da API; o tests/atendimento-automaticas.cjs confere
// campo a campo) e as regras puras; embaixo, a tela.
import { duracao, erroDaApi, limiteDe, tamanhoDoEnvio } from '~/components/AtendimentoPlataforma.vue'

export type ModoAutomacao = 'desligado' | 'simular' | 'enviar'
export type PlataformaAutomacao = 'shopee' | 'tiktok' | 'ml'

// = routers/atendimento_automacoes.chaves()
export interface ChavesAutomacao {
  leitura_ativa: boolean
  motor_ativo: boolean
  envio_automacoes: boolean
  envio_geral: boolean
  shopee_auto_reply: boolean
  shopee_auto_reply_adaptador: boolean
  shopee_mensagens_comprador: boolean
  teto_dia: number
}
export interface FaixaAutomacao {
  nivel: 'info' | 'aviso' | 'erro'
  texto: string
}
// = a CONTA de automacoes_comparar.estatisticas/somar (sem as internas).
export interface ContaAutomacao {
  total: number
  simulado: number
  enviado: number
  pulado: number
  falhou: number
  agendado: number
  pendente: number
  bateu_mandou: number
  bateu_nao_mandou: number
  so_davinci: number
  so_davinci_2d: number
  so_duoke: number
  combinada: number
  alertas: number
  texto_invalido: number
  envio_desligado: number
  diferenca_mediana_s: number | null
  atraso_mediana_s: number | null
  motivos: Record<string, number>
  casos: number
  precisao: number | null
  cobertura: number | null
  concordancia: number | null
  pode_trocar: boolean
  por_que_nao: string[]
  // Os 7 dias de DADOS da regra (automacoes_comparar.com_os_dias): na lista vêm sempre.
  dados_desde?: string | null
  dias_de_dados?: number
  completa_em?: string | null
  faltam_h?: number
}
export interface ParteAutomacao {
  tipo: 'texto' | 'cartao_pedido' | 'figurinha' | 'resposta_publica'
  texto?: string | null
  figurinha?: string | null
  pacote?: string | null
}
// = _regra_out
export interface RegraAutomacao {
  id: string | null
  modo: ModoAutomacao
  padrao: boolean
  partes: ParteAutomacao[]
  atraso_min: number
  janela_inicio: string | null
  janela_fim: string | null
  condicoes: Record<string, boolean | number>
  teto_dia: number | null
  versao: number
  ligada_desde: string | null
  enviar_desde: string | null
  disjuntor_em: string | null
  disjuntor_motivo: string | null
  atualizado_em: string | null
}
export interface UltimoAutomacao {
  devido_em: string
  estado: string
  motivo: string | null
}
// = a loja de listar_automacoes
export interface LojaAutomacao {
  integration_id: string
  loja: string
  integracao: string
  canal_status: string | null
  canal_modo: string | null
  sem_acesso: boolean
  duoke_hoje: boolean
  regra: RegraAutomacao
  h24: ContaAutomacao | null
  periodo: ContaAutomacao | null
  ultimo: UltimoAutomacao | null
  pode_enviar: boolean
  por_que_nao_enviar: string[]
  pode_trocar: boolean
  por_que_nao_trocar: string[]
  // O critério também pede 7 dias de dados da regra nesta loja: desde quando
  // há dados, quantos dias, quando completa e quantas horas faltam (0 = completou).
  dados_desde: string | null
  dias_de_dados: number
  completa_em: string | null
  faltam_h: number
}
// = _aut_out + os totais e as lojas
export interface Automacao {
  codigo: string
  plataforma: string
  canal: string
  nome: string
  descricao: string
  tipo: string
  gatilho: string
  alvo: string
  familia: string
  campanha: boolean
  travada: boolean
  diferenca_combinada: string | null
  atraso_min: number
  validade_min: number
  janela_inicio: string | null
  janela_fim: string | null
  condicoes_padrao: Record<string, boolean | number>
  placeholders: Record<string, string>
  seguinte: string | null
  so_simulacao: string | null
  so_simulacao_texto: string | null
  total_24h: ContaAutomacao | null
  total_periodo: ContaAutomacao | null
  lojas: LojaAutomacao[]
}
// = GET /api/atendimento/automacoes
export interface AutomacoesResposta {
  chaves: ChavesAutomacao
  faixa: FaixaAutomacao
  dias: number
  motivos: Record<string, string>
  divergencias: Record<string, string>
  alertas: Record<string, string>
  automacoes: Automacao[]
}
// = _linha_out (o registro: SEM texto nenhum)
export interface RegistroLinha {
  id: string
  automacao: string
  automacao_nome: string
  plataforma: string
  integration_id: string
  loja: string | null
  alvo: string
  pedido: string | null
  conversa_id: string | null
  evento_em: string | null
  visto_em: string | null
  devido_em: string
  decidido_em: string | null
  estado: string
  modo: string | null
  motivo: string | null
  motivo_texto: string | null
  erro: string | null
  duoke: string
  duoke_em: string | null
  duoke_diferenca_s: number | null
  divergencia: string | null
  divergencia_texto: string | null
  alerta: string | null
  alerta_texto: string | null
  tentativas: number
  regra_versao: number | null
}
export interface RegistroResposta {
  linhas: RegistroLinha[]
  proximo: string | null
}
// = PATCH /automacoes/{automacao}/{integration_id}
export interface RegraMudou {
  integration_id: string
  automacao: string
  regra: RegraAutomacao
  rearmadas: number
  pode_enviar: boolean
  por_que_nao_enviar: string[]
}
// = POST /automacoes/{automacao}/simular-nas-lojas-do-duoke
export interface SimularNasLojas {
  criadas: number
  ligadas: number
  mantidas: number
}
// = POST /automacoes/previa
export interface PreviaResposta {
  partes: ParteAutomacao[]
  sem_nome: ParteAutomacao[]
  motivos: string[]
  comprador_exemplo: string
}
// = GET /automacoes/registro/{id}/previa — "como o cliente receberia" (automacoes_previa.py).
// Uma parte da prévia (o mesmo desenho dos dois lados: DaVinci e Duoke).
export interface ParteVista {
  tipo: 'texto' | 'cartao_pedido' | 'figurinha' | 'resposta_publica' | 'outro'
  texto: string | null
  pedido: string | null
  figurinha: string | null
  imagem_url: string | null
  em: string | null
  diferenca_s: number | null
  principal: boolean
  nota: string | null
}
export interface PreviaAutomacaoLinha {
  codigo: string
  nome: string
  plataforma: string
  tipo: string | null
  so_simulacao: string | null
  so_simulacao_texto: string | null
}
export interface PreviaDavinci {
  sairia: boolean
  de_verdade: boolean
  estado: string
  motivo: string | null
  motivo_texto: string | null
  hora: string | null
  hora_tipo: 'saiu' | 'sairia' | 'devido'
  comprador: string | null
  valores: Record<string, string>
  partes: ParteVista[]
  motivos_validador: string[]
  versao_regra: number | null
  versao_da_linha: number | null
}
export interface PreviaDuoke {
  estado: string
  comparacao: Comparacao | null
  diferenca_s: number | null
  em: string | null
  janela_de: string | null
  janela_ate: string | null
  partes: ParteVista[]
}
export interface PreviaRegistro {
  linha: RegistroLinha
  automacao: PreviaAutomacaoLinha
  davinci: PreviaDavinci
  duoke: PreviaDuoke
}
// = RegraIn (o corpo do PATCH: tudo opcional)
export interface CorpoRegra {
  modo?: ModoAutomacao
  partes?: ParteAutomacao[]
  atraso_min?: number
  janela_inicio?: string
  janela_fim?: string
  sem_janela?: boolean
  condicoes?: Record<string, boolean | number>
  teto_dia?: number
  sem_teto?: boolean
  desliguei_no_duoke?: boolean
  troca_sem_criterio?: boolean
}

// O nome de exemplo da prévia (= NOME_EXEMPLO do router).
export const NOME_EXEMPLO = 'maria.silva'
// O valor de exemplo das lacunas próprias de uma automação (= EXEMPLOS_EXTRA do catálogo).
export const EXEMPLOS_LACUNA: Record<string, string> = { valor_cupom: '20' }
// A resposta PÚBLICA da avaliação da Shopee: 500 caracteres (= LIMITE_CARACTERES do
// backend, ("shopee", "avaliacao")) — a mensagem do chat segue o limite da caixa.
export const LIMITE_RESPOSTA_PUBLICA = 500
// Quantas linhas do registro por página (a API aceita até 500).
export const LIMITE_REGISTRO = 200
export const PERIODOS = [7, 15, 30]

export const PLATAFORMAS_AUTOMACAO: { value: PlataformaAutomacao; nome: string }[] = [
  { value: 'shopee', nome: 'Shopee' },
  { value: 'tiktok', nome: 'TikTok' },
  { value: 'ml', nome: 'Mercado Livre' },
]

export const MODOS_AUTOMACAO: { value: ModoAutomacao; label: string; hint: string; cls: string }[] = [
  { value: 'desligado', label: 'Desligado', hint: 'não roda nesta loja', cls: 'bg-muted text-muted-foreground' },
  { value: 'simular', label: 'Simular', hint: 'modo seco: o DaVinci registra o que mandaria e compara com o Duoke — nada sai', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  { value: 'enviar', label: 'Enviar', hint: 'manda de verdade — só depois de desligar esta automação desta loja no Duoke', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
]
export function modoAutomacaoInfo(modo: string | null | undefined) {
  return MODOS_AUTOMACAO.find((m) => m.value === modo) ?? { value: (modo || '') as ModoAutomacao, label: modo || '—', hint: '', cls: 'bg-muted text-muted-foreground' }
}

// O "quando" de cada automação (catalogo.GATILHO_*).
export const GATILHOS: Record<string, string> = {
  mensagem: 'mensagem do comprador',
  opcao: 'o comprador escolhe a opção do menu',
  seguinte: 'depois da anterior',
  pedido_pago: 'pedido pago (Bling)',
  entregue: 'pedido entregue (Logística)',
  concluido: 'pedido concluído (Logística)',
  pedido_nao_pago: 'pedido criado e não pago (o índice de pedidos, de hora em hora)',
  avaliacao: 'avaliação do comprador (lida a cada 30 min)',
  pedido_tiktok: 'aviso de pedido da TikTok',
}
// Para quem é a linha do registro (o `alvo`).
export const ALVOS: Record<string, string> = {
  conversa: 'conversa',
  comprador: 'comprador',
  pedido: 'pedido',
  mensagem: 'mensagem',
  duoke: 'mensagem do Duoke',
  avaliacao: 'avaliação',
}

// Por que "Enviar" não pode agora (_por_que_nao_enviar e o 409 do PATCH).
export const POR_QUE_NAO_ENVIAR: Record<string, string> = {
  so_simulacao: 'esta automação só simula (mostra o que o DaVinci mandaria): não vai para Enviar',
  sem_texto: 'esta opção ainda não tem texto (o painel do Duoke diz qual é)',
  envio_desligado: 'o envio das automáticas está desligado no servidor (ATENDIMENTO_AUTOMACOES_ENVIO)',
  envio_geral_desligado: 'o envio geral pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO)',
  campanha_sem_auto_reply: 'campanha da Shopee: só sai como resposta automática da Shopee — falta a confirmação (ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY) ou o envio por ela no DaVinci',
  shopee_mensagens_desligadas: 'a mensagem ao comprador da Shopee está desligada no servidor (SHOPEE_MENSAGENS_COMPRADOR)',
  loja_sem_acesso: 'a leitura desta loja está sem acesso ou desligada',
}
export function motivosEnviar(codigos: string[] | null | undefined): string[] {
  return (codigos || []).map((c) => POR_QUE_NAO_ENVIAR[c] || c)
}

// Por que o DaVinci voltou a regra para Simular sozinho (`disjuntor_motivo`).
export const DISJUNTOR_MOTIVOS: Record<string, string> = {
  duoke_ainda_ligado: 'o Duoke ainda mandou (Duoke ainda ligado?)',
  parte_falhou: 'a mensagem saiu pela metade (uma parte saiu e a seguinte não) — confira a linha em Revisar',
  plataforma_recusou: 'a Shopee recusou a campanha (o formato ou a permissão da resposta automática)',
}
export function tituloDisjuntor(em: string, motivo: string | null | undefined, quando: string): string {
  const porque = (motivo && DISJUNTOR_MOTIVOS[motivo]) || motivo || DISJUNTOR_MOTIVOS.duoke_ainda_ligado
  return `O DaVinci voltou esta regra para Simular em ${quando || em}: ${porque}`
}

// Os `detail.code` das rotas da aba → a frase da tela.
export const ERROS_AUTOMACAO: Record<string, string> = {
  ...Object.fromEntries(Object.entries(POR_QUE_NAO_ENVIAR).map(([k, v]) => [k, `Não dá para pôr em Enviar: ${v}.`])),
  sem_texto: 'Esta opção ainda não tem texto: fica desligada até o painel do Duoke dizer qual é.',
  confirmar_duoke: 'Marque que você já desligou esta automação desta loja no Duoke.',
  criterio_nao_passou: 'O critério da troca não passou nesta loja nos últimos 7 dias: marque que você sabe disso para trocar mesmo assim.',
  texto_invalido: 'O texto não passa no validador',
  parte_invalida: 'A resposta pública só existe na resposta da avaliação.',
  janela_invalida: 'Horário inválido: o início tem que ser antes do fim (HH:MM).',
  condicao_desconhecida: 'Condição desconhecida para esta automação.',
  condicao_invalida: 'Valor inválido numa condição.',
  automacao_nao_encontrada: 'Automação não encontrada (atualize a tela).',
  registro_nao_encontrado: 'Linha do registro não encontrada (fora da sua equipe, ou já saiu do registro).',
  loja_nao_encontrada: 'Loja não encontrada (fora da sua equipe ou de outra plataforma).',
}

// As condições do catálogo (os interruptores e números de cada automação).
export const CONDICOES: Record<string, { label: string; unidade?: string; hint?: string }> = {
  sessao_h: { label: 'O menu vale por', unidade: 'h', hint: 'não manda outro menu enquanto o último valer; a opção conta com o menu valendo' },
  opcao_sem_sessao: { label: 'Responder a opção mesmo com o menu vencido', hint: 'como o Duoke: quem já conhece o menu recebe a resposta na hora' },
  pular_em_disputa_com_pessoa: { label: 'Não mandar em reclamação/devolução com alguém da equipe atendendo (24 h)' },
  intervalo_h: { label: 'No máximo um a cada', unidade: 'h' },
  so_se_comprador_por_ultimo: { label: 'Só se a última fala for do comprador' },
  ciclo_dias: { label: 'O ciclo recomeça depois de', unidade: 'dias' },
  so_quem_nunca_comprou: { label: 'Só para quem nunca comprou na loja' },
  nao_se_pessoa_respondeu: { label: 'Não mandar se alguém da equipe já respondeu' },
  so_com_cartao_de_produto: { label: 'Só se o comprador mandou o cartão de um produto' },
  recomeca_no_cartao_depois_da_segunda: { label: 'Depois da de 26 h, o próximo cartão de produto abre outro ciclo', hint: 'como o Duoke (medido em 06/10): texto solto continua no ciclo de 7 dias' },
  so_sem_avaliacao: { label: 'Só se o comprador ainda não avaliou' },
  so_com_conversa: { label: 'Só se já houver conversa com o comprador' },
  um_por_comprador_h: { label: 'Um por comprador a cada', unidade: 'h', hint: 'como o Duoke: quem já recebeu um nas últimas 24 h não recebe outro (o Duoke manda de novo a partir de ~27 h)' },
}
export function rotuloCondicao(chave: string): string {
  return CONDICOES[chave]?.label || chave
}

// O selo da diferença combinada de propósito (catalogo.diferenca_combinada).
export const SELOS_COMBINADA: Record<string, string> = {
  na_hora: 'Responde na hora (o Duoke responde 12 h depois do menu, e só se ninguém respondeu)',
  horario_comercial: 'Sai das 9h às 20h (o Duoke manda de madrugada)',
  visto_de_hora_em_hora: 'O DaVinci vê o pedido não pago de hora em hora (o índice de pedidos): sai até ~1 h depois dos 30 min do Duoke',
}

// O estado da linha do registro (catalogo.ESTADOS).
const CLS_CINZA = 'bg-muted text-muted-foreground'
const CLS_AZUL = 'bg-sky-500/15 text-sky-700 dark:text-sky-300'
const CLS_VIOLETA = 'bg-violet-500/15 text-violet-700 dark:text-violet-300'
const CLS_AMBAR = 'bg-amber-500/15 text-amber-800 dark:text-amber-300'
const CLS_VERDE = 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
const CLS_VERMELHO = 'bg-red-500/15 text-red-700 dark:text-red-300'
export const ESTADOS_REGISTRO: Record<string, { label: string; hint: string; cls: string }> = {
  agendado: { label: 'agendado', hint: 'esperando a hora de decidir', cls: CLS_AZUL },
  simulado: { label: 'mandaria', hint: 'modo seco: o DaVinci mandaria nesta hora (nada saiu)', cls: CLS_VIOLETA },
  enviando: { label: 'enviando', hint: 'saindo para a plataforma agora', cls: CLS_AMBAR },
  enviado: { label: 'enviado', hint: 'saiu pelo DaVinci', cls: CLS_VERDE },
  pulado: { label: 'não mandaria', hint: 'a regra decidiu não mandar (veja o motivo)', cls: CLS_CINZA },
  falhou: { label: 'falhou', hint: 'a plataforma recusou — não chegou ao comprador', cls: CLS_VERMELHO },
  revisar: { label: 'a conferir', hint: 'a plataforma não confirmou — pode ter saído; confira na conversa', cls: CLS_AMBAR },
  so_duoke: { label: 'só o Duoke', hint: 'o Duoke mandou e o DaVinci não tinha esta linha', cls: CLS_AMBAR },
}
// O que o Duoke fez (catalogo.DUOKE_*).
export const DUOKE_REGISTRO: Record<string, { label: string; hint: string }> = {
  pendente: { label: 'conferindo', hint: 'esperando a leitura da loja passar da janela para saber se o Duoke mandou' },
  mandou: { label: 'Duoke mandou', hint: 'o Duoke mandou a mesma automação' },
  nao_mandou: { label: 'Duoke não mandou', hint: 'a janela fechou sem a mensagem do Duoke' },
  nao_se_aplica: { label: '—', hint: 'não se compara com o Duoke' },
}

export type Comparacao = 'bateu' | 'so_davinci' | 'so_duoke' | 'combinada' | 'pendente'
export const COMPARACOES: Record<Comparacao, { label: string; hint: string; cls: string }> = {
  bateu: { label: 'bateu', hint: 'o DaVinci e o Duoke fizeram o mesmo (os dois mandaram, ou nenhum)', cls: CLS_VERDE },
  so_davinci: { label: 'só DaVinci', hint: 'o DaVinci mandaria e o Duoke não mandou', cls: CLS_AMBAR },
  so_duoke: { label: 'só Duoke', hint: 'o Duoke mandou e o DaVinci não mandaria', cls: CLS_AMBAR },
  combinada: { label: 'combinada', hint: 'diferença combinada de propósito: fica fora da conta', cls: CLS_CINZA },
  pendente: { label: 'conferindo', hint: 'o comparador ainda não sabe se o Duoke mandou', cls: CLS_CINZA },
}
// O filtro "comparação" do registro: `so` da API; `duoke:` vira o `duoke`.
export const FILTROS_COMPARACAO: { value: string; label: string }[] = [
  { value: '', label: 'toda comparação' },
  { value: 'bateu', label: 'bateu' },
  { value: 'so_davinci', label: 'só DaVinci' },
  { value: 'so_duoke', label: 'só Duoke' },
  { value: 'alerta', label: 'com alerta' },
  { value: 'combinada', label: 'diferença combinada' },
  { value: 'duoke:pendente', label: 'ainda conferindo' },
]

// O que manda (o comparador conta como "mandaria"): automacoes_comparar._ESTADOS_QUE_MANDAM.
const ESTADOS_QUE_MANDAM = ['simulado', 'enviado', 'enviando', 'revisar']

// ─── números ────────────────────────────────────────────────────────────────
// 0,987 → "98,7%". Para BAIXO: 94,96% não pode aparecer como "95%" verde.
export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  const p = Math.floor(Number(v) * 1000) / 10
  return `${String(p.toFixed(1)).replace('.', ',').replace(/,0$/, '')}%`
}
// Verde a partir de 95% (o critério da troca).
export function nivelPct(v: number | null | undefined): 'bom' | 'quase' | 'ruim' | null {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return null
  if (v >= 0.95) return 'bom'
  if (v >= 0.85) return 'quase'
  return 'ruim'
}
export function clsPct(v: number | null | undefined): string {
  const n = nivelPct(v)
  if (n === 'bom') return 'font-semibold text-emerald-700 dark:text-emerald-300'
  if (n === 'quase') return 'font-semibold text-amber-700 dark:text-amber-300'
  if (n === 'ruim') return 'font-semibold text-red-600 dark:text-red-400'
  return 'text-muted-foreground'
}
// A "% que bateu": a MENOR entre precisão e cobertura (o critério exige as
// duas; a concordância soma "nenhum dos dois mandou" e pintava de verde uma
// cobertura de 94%). Nas OPÇÕES, a cobertura (a precisão não se mede pelo
// Duoke — ele responde 12 h depois e só se ninguém respondeu).
export type MedidaBateu = 'precisão' | 'cobertura'
function numeroOuNulo(v: number | null | undefined): number | null {
  return v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v)
}
export function bateuDe(c: ContaAutomacao | null | undefined, tipo: string): { valor: number | null; medida: MedidaBateu; casos: number } {
  if (!c) return { valor: null, medida: 'cobertura', casos: 0 }
  const cobertura = numeroOuNulo(c.cobertura)
  if (tipo === 'opcao') return { valor: cobertura, medida: 'cobertura', casos: c.casos }
  const precisao = numeroOuNulo(c.precisao)
  if (precisao === null || cobertura === null) {
    // Sem uma das duas (nenhum "os dois mandaram"), vale a que existe.
    return { valor: precisao ?? cobertura, medida: precisao === null ? 'cobertura' : 'precisão', casos: c.casos }
  }
  return precisao < cobertura
    ? { valor: precisao, medida: 'precisão', casos: c.casos }
    : { valor: cobertura, medida: 'cobertura', casos: c.casos }
}
// "Mandaria" = o que o motor decidiu mandar (no seco, simulado; depois, enviado).
export function mandaria(c: ContaAutomacao | null | undefined): number {
  return c ? (c.simulado || 0) + (c.enviado || 0) : 0
}
// duoke_diferenca_s = Duoke − o nosso horário. Na coluna "Duoke" do registro
// o nome já está em cima (`comNome` = false).
export function fmtDiferenca(s: number | null | undefined, comNome = true): string {
  if (s === null || s === undefined || !Number.isFinite(Number(s))) return ''
  const quando = Math.abs(s) < 60 ? 'na mesma hora' : s > 0 ? `${duracao(s / 60)} depois` : `${duracao(-s / 60)} antes`
  return comNome ? `Duoke ${quando}` : quando
}
// O atraso REAL do DaVinci: do gatilho até a hora em que a mensagem sai (ou
// sairia no modo seco) — a mediana da conta (`atraso_mediana_s`).
export function fmtAtrasoReal(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(Number(s))) return ''
  return Number(s) < 60 ? 'menos de 1 min' : duracao(Number(s) / 60)
}
export function fmtAtraso(min: number | null | undefined): string {
  const m = Number(min) || 0
  return m <= 0 ? 'na hora' : duracao(m)
}
export function janelaLegivel(inicio: string | null | undefined, fim: string | null | undefined): string {
  return inicio && fim ? `das ${inicio} às ${fim}` : 'o dia todo'
}
export function gatilhoLegivel(aut: Pick<Automacao, 'codigo' | 'gatilho'>, todas: Pick<Automacao, 'codigo' | 'nome' | 'seguinte'>[] = []): string {
  if (aut.gatilho === 'seguinte') {
    const antes = todas.find((a) => a.seguinte === aut.codigo)
    if (antes) return `depois de ${antes.nome}`
  }
  return GATILHOS[aut.gatilho] || aut.gatilho
}

// O title do "% que bateu": a conta inteira, em português.
export function tituloConta(c: ContaAutomacao | null | undefined, tipo: string, periodo: string, motivos: Record<string, string> = {}): string {
  if (!c) return `${periodo}: nada no registro.`
  const b = bateuDe(c, tipo)
  const linhas = [
    `${periodo}: ${c.total} no registro — ${mandaria(c)} mandaria${c.enviado ? ` (${c.enviado} enviadas)` : ''}, ${c.pulado} não mandaria`,
    b.valor === null
      ? 'Bateu com o Duoke: sem caso para comparar ainda'
      : `Bateu com o Duoke: ${fmtPct(b.valor)} (${tipo === 'opcao' ? 'a cobertura' : `a menor entre precisão e cobertura: a ${b.medida}`}) em ${c.casos} ${c.casos === 1 ? 'caso' : 'casos'}`,
  ]
  if (tipo === 'opcao') {
    linhas.push(`Cobertura ${fmtPct(c.cobertura)}: nas opções a precisão não se mede pelo Duoke (ele responde 12 h depois e só se ninguém respondeu)`)
    if (c.so_davinci) linhas.push(`A mais que o Duoke: o DaVinci responderia na hora ${c.so_davinci} ${c.so_davinci === 1 ? 'vez' : 'vezes'} que o Duoke não respondeu (de propósito: ele responde 12 h depois e só se ninguém respondeu)`)
  } else {
    linhas.push(`Precisão ${fmtPct(c.precisao)} (não manda o que o Duoke não manda) · cobertura ${fmtPct(c.cobertura)} (manda o que o Duoke manda) · concordância ${fmtPct(c.concordancia)} (conta também "nenhum dos dois mandou")`)
  }
  linhas.push(`Bateu: ${c.bateu_mandou} os dois mandaram, ${c.bateu_nao_mandou} nenhum dos dois`)
  linhas.push(`Só DaVinci: ${c.so_davinci}${c.so_davinci_2d ? ` (${c.so_davinci_2d} nos últimos 2 dias)` : ''} · só Duoke: ${c.so_duoke}`)
  if (c.combinada) linhas.push(`Diferenças combinadas (fora da conta): ${c.combinada}`)
  if (c.pendente) linhas.push(`Ainda conferindo com o Duoke: ${c.pendente}`)
  if (c.alertas) linhas.push(`ALERTA: ${c.alertas} — mandaria para quem devolveu, cancelou ou reclamou`)
  if (c.texto_invalido) linhas.push(`Texto que não passa no validador: ${c.texto_invalido}`)
  if (c.envio_desligado) linhas.push(`Em Enviar com a chave desligada (não saiu): ${c.envio_desligado}`)
  if (c.diferenca_mediana_s !== null && c.diferenca_mediana_s !== undefined) linhas.push(`Horário: ${fmtDiferenca(c.diferenca_mediana_s)} da nossa (mediana)`)
  if (c.atraso_mediana_s !== null && c.atraso_mediana_s !== undefined) linhas.push(`Atraso do DaVinci: ${fmtAtrasoReal(c.atraso_mediana_s)} do gatilho até sair (mediana)`)
  const top = Object.entries(c.motivos || {}).slice(0, 3)
  if (top.length) linhas.push(`Não mandaria por: ${top.map(([m, n]) => `${motivos[m] || m} (${n})`).join('; ')}`)
  // A troca vale pelo critério da LOJA nos últimos 7 dias (o selo da linha);
  // aqui é o critério aplicado ao período escolhido, só para ler.
  linhas.push(c.pode_trocar ? `Critério em ${periodo}: passa (≥ 95%, 30 casos, 7 dias de dados, sem alerta) — a troca vale pelos últimos 7 dias de cada loja` : `Critério em ${periodo}: ainda não — ${(c.por_que_nao || []).join('; ')}`)
  return linhas.join('\n')
}

// ─── modo ───────────────────────────────────────────────────────────────────
export function chaveLoja(aut: Pick<Automacao, 'codigo'>, loja: Pick<LojaAutomacao, 'integration_id'>): string {
  return `${aut.codigo}:${loja.integration_id}`
}
// As opções do seletor: "Enviar" desabilitado (com o porquê) enquanto a API
// disser que não pode; a opção sem texto não sai de Desligado.
export function opcoesDeModo(loja: Pick<LojaAutomacao, 'regra' | 'pode_enviar' | 'por_que_nao_enviar'>, aut: Pick<Automacao, 'travada'>) {
  const atual = loja.regra.modo
  return MODOS_AUTOMACAO.map((m) => {
    if (m.value !== atual && m.value !== 'desligado' && aut.travada) {
      return { value: m.value, label: `${m.label} (sem texto)`, disabled: true, title: POR_QUE_NAO_ENVIAR.sem_texto }
    }
    if (m.value === 'enviar' && atual !== 'enviar' && !loja.pode_enviar) {
      const porque = motivosEnviar(loja.por_que_nao_enviar)
      return { value: m.value, label: `${m.label} (travado)`, disabled: true, title: `Travado: ${porque.join('; ') || 'a API não deixa agora'}` }
    }
    return { value: m.value, label: m.label, disabled: false, title: m.hint }
  })
}
// Regra em ENVIAR com o envio desligado no servidor: nada sai — e, se o Duoke
// já foi desligado nessa loja, o comprador fica sem.
export function enviarSemChave(loja: Pick<LojaAutomacao, 'regra'>, ch: Pick<ChavesAutomacao, 'envio_automacoes' | 'envio_geral'> | null | undefined): boolean {
  return loja.regra.modo === 'enviar' && !(ch?.envio_automacoes && ch?.envio_geral)
}
export function contarModos(lojas: Pick<LojaAutomacao, 'regra'>[]): Record<ModoAutomacao, number> {
  const out: Record<ModoAutomacao, number> = { desligado: 0, simular: 0, enviar: 0 }
  for (const l of lojas) out[l.regra.modo] = (out[l.regra.modo] || 0) + 1
  return out
}
export type Mostrar = 'todas' | 'ligadas' | 'duoke'
export const MOSTRAR: { value: Mostrar; label: string }[] = [
  { value: 'todas', label: 'todas as lojas' },
  { value: 'ligadas', label: 'só as ligadas (simular/enviar)' },
  { value: 'duoke', label: 'só onde o Duoke manda hoje' },
]
export function lojasVisiveis<T extends Pick<LojaAutomacao, 'loja' | 'integracao' | 'regra' | 'duoke_hoje'>>(lojas: T[], busca: string, mostrar: Mostrar): T[] {
  const q = (busca || '').trim().toLowerCase()
  return lojas
    .filter((l) => mostrar !== 'ligadas' || l.regra.modo !== 'desligado')
    .filter((l) => mostrar !== 'duoke' || l.duoke_hoje)
    .filter((l) => !q || l.loja.toLowerCase().includes(q) || (l.integracao || '').toLowerCase().includes(q))
}
// A troca é LOJA por loja (o critério de cada uma, em 7 dias): a automação só
// conta quantas lojas ligadas estão prontas.
export function lojasProntas(lojas: Pick<LojaAutomacao, 'regra' | 'pode_trocar'>[]): { prontas: number; ligadas: number } {
  const ligadas = lojas.filter((l) => l.regra.modo !== 'desligado')
  return { prontas: ligadas.filter((l) => l.pode_trocar).length, ligadas: ligadas.length }
}
// O que falta dos 7 dias de dados (= automacoes_comparar.texto_falta).
export function textoFalta(faltamH: number): string {
  if (faltamH < 24) return `falta${faltamH === 1 ? '' : 'm'} ${faltamH} h`
  const d = Math.max(1, Math.floor(faltamH / 24 + 0.5))
  return `falta${d === 1 ? '' : 'm'} ${d} ${d === 1 ? 'dia' : 'dias'}`
}
// O selo da troca na linha da loja: "faltam N dias" enquanto não houver os 7 dias de dados.
export function seloTroca(loja: Pick<LojaAutomacao, 'pode_trocar' | 'faltam_h'>): string {
  if (loja.pode_trocar) return 'pronta para trocar'
  return loja.faltam_h > 0 ? `troca: ${textoFalta(loja.faltam_h)}` : 'troca: ainda não'
}
export function tituloTroca(loja: Pick<LojaAutomacao, 'pode_trocar' | 'por_que_nao_trocar'> & Partial<Pick<LojaAutomacao, 'faltam_h' | 'completa_em'>>, fmt: (iso: string) => string = (iso) => iso): string {
  if (loja.pode_trocar) return 'Pronta para trocar nesta loja pelo critério dos últimos 7 dias (≥ 95% em 30 casos e 7 dias de dados, sem alerta; nas opções, a cobertura)'
  const completa = loja.faltam_h && loja.completa_em ? ` (os 7 dias de dados completam ${fmt(loja.completa_em)})` : ''
  return `Troca nesta loja (últimos 7 dias): ainda não — ${(loja.por_que_nao_trocar || []).join('; ') || 'sem conta'}${completa}`
}
// O botão "Simular nas lojas do Duoke" só tem o que fazer nas desligadas de lá.
export function faltaSimularNoDuoke(lojas: Pick<LojaAutomacao, 'duoke_hoje' | 'regra'>[]): number {
  return lojas.filter((l) => l.duoke_hoje && l.regra.modo === 'desligado').length
}

// As chaves do servidor, em chips (o que cada uma quer dizer no title).
export type ChipChave = { chave: string; texto: string; hint: string; tom: 'ok' | 'neutro' | 'atencao' }
export function chipsDasChaves(ch: ChavesAutomacao | null | undefined, plataforma: string): ChipChave[] {
  if (!ch) return []
  const out: ChipChave[] = []
  if (!ch.leitura_ativa) out.push({ chave: 'leitura', texto: 'Leitura das lojas desligada', hint: 'ATENDIMENTO_LEITURA_ATIVA: sem leitura, o motor não vê mensagem nova', tom: 'atencao' })
  out.push(ch.motor_ativo
    ? { chave: 'motor', texto: 'Motor ligado', hint: 'ATENDIMENTO_AUTOMACOES_ATIVA: o motor roda a cada 2 min', tom: 'ok' }
    : { chave: 'motor', texto: 'Motor desligado', hint: 'ATENDIMENTO_AUTOMACOES_ATIVA desligada: nada é simulado nem enviado', tom: 'atencao' })
  out.push(ch.envio_automacoes
    ? { chave: 'envio_automacoes', texto: 'Envio das automáticas LIGADO', hint: 'ATENDIMENTO_AUTOMACOES_ENVIO: as regras em Enviar mandam de verdade', tom: 'atencao' }
    : { chave: 'envio_automacoes', texto: 'Envio das automáticas desligado', hint: 'ATENDIMENTO_AUTOMACOES_ENVIO: nenhuma regra manda de verdade (Enviar fica travado)', tom: 'neutro' })
  out.push(ch.envio_geral
    ? { chave: 'envio_geral', texto: 'Envio geral ligado', hint: 'ATENDIMENTO_ENVIO_ATIVO: o freio único está solto', tom: 'atencao' }
    : { chave: 'envio_geral', texto: 'Envio geral desligado', hint: 'ATENDIMENTO_ENVIO_ATIVO: o freio único — desligado, nada sai pelo DaVinci', tom: 'neutro' })
  if (plataforma === 'shopee') {
    if (!ch.shopee_auto_reply) {
      out.push({ chave: 'auto_reply', texto: 'Resposta automática da Shopee não confirmada', hint: 'ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY: sem ela (e sem o envio por ela no DaVinci) as campanhas (convite, dúvida, pedido recebido, entregue, pós) não vão para Enviar', tom: 'neutro' })
    } else if (!ch.shopee_auto_reply_adaptador) {
      out.push({ chave: 'auto_reply', texto: 'Resposta automática da Shopee: falta o envio por ela', hint: 'ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY está ligada, mas o DaVinci ainda não manda como resposta automática da Shopee: as campanhas continuam fora do Enviar (nunca saem como mensagem normal)', tom: 'atencao' })
    } else {
      out.push({ chave: 'auto_reply', texto: 'Resposta automática da Shopee confirmada', hint: 'ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY e o envio por ela: as campanhas podem ir para Enviar', tom: 'ok' })
    }
    if (!ch.shopee_mensagens_comprador) out.push({ chave: 'shopee_mensagens', texto: 'Mensagem ao comprador da Shopee desligada', hint: 'SHOPEE_MENSAGENS_COMPRADOR: nada da Shopee vai para Enviar', tom: 'neutro' })
  }
  out.push(ch.teto_dia > 0
    ? { chave: 'teto', texto: `Teto: ${ch.teto_dia} por dia`, hint: 'ATENDIMENTO_AUTOMACOES_TETO_DIA: por loja e família (conversa/pedido), quando a regra não tem teto próprio', tom: 'neutro' }
    : { chave: 'teto', texto: 'Sem teto do dia', hint: 'ATENDIMENTO_AUTOMACOES_TETO_DIA = 0', tom: 'neutro' })
  return out
}
export const TOM_CHIP: Record<ChipChave['tom'], string> = {
  ok: 'border-emerald-500/40 text-emerald-800 dark:text-emerald-300',
  neutro: 'border-border text-muted-foreground',
  atencao: 'border-amber-500/50 text-amber-800 dark:text-amber-300',
}
// As grades da tela larga (no celular, as linhas empilham).
export const GRADE_LOJA = 'md:grid-cols-[minmax(0,1.5fr)_11rem_3.5rem_3.5rem_5.5rem_4.5rem_4.5rem_minmax(0,1fr)]'
export const GRADE_REGISTRO = 'md:grid-cols-[7rem_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.1fr)_2rem]'
export const CLS_TROCA_PRONTA = 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
export const FAIXA_CLS: Record<string, string> = {
  info: 'border-sky-500/40 bg-sky-500/10 text-sky-900 dark:text-sky-200',
  aviso: 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200',
  erro: 'border-red-500/50 bg-red-500/10 text-red-800 dark:text-red-300',
}

// ─── texto da regra ─────────────────────────────────────────────────────────
// As {lacunas} do texto (o mesmo desenho do validador do backend: `{x}` e `{{x}}`).
const RE_LACUNA = /\{\{?\s*([a-z_]+)\s*\}?\}/gi
export function lacuna(nome: string | number): string {
  return `{${nome}}`
}
export function placeholdersDoTexto(texto: string | null | undefined): string[] {
  return [...new Set([...(texto || '').matchAll(RE_LACUNA)].map((m) => m[1].toLowerCase()))]
}
export function placeholdersDesconhecidos(texto: string | null | undefined, conhecidos: string[]): string[] {
  return placeholdersDoTexto(texto).filter((p) => !conhecidos.includes(p))
}
// O tamanho como sai, com o nome de exemplo no lugar do {comprador} (e o valor
// de exemplo nas lacunas próprias, como o {valor_cupom}).
export function tamanhoComExemplo(texto: string | null | undefined, plataforma: string): number {
  let t = (texto || '').split('{comprador}').join(NOME_EXEMPLO)
  for (const [k, v] of Object.entries(EXEMPLOS_LACUNA)) t = t.split(`{${k}}`).join(v)
  return tamanhoDoEnvio(t, plataforma)
}
// As partes que têm texto (o chat e a resposta pública da avaliação).
export const TIPOS_COM_TEXTO = ['texto', 'resposta_publica']
export function temTexto(p: Pick<ParteAutomacao, 'tipo'>): boolean {
  return TIPOS_COM_TEXTO.includes(p.tipo)
}
// O limite da parte: a resposta pública da avaliação tem o dela.
export function limiteDaParte(aut: Pick<Automacao, 'plataforma' | 'canal'>, p: Pick<ParteAutomacao, 'tipo'>): number | null {
  if (p.tipo === 'resposta_publica') return LIMITE_RESPOSTA_PUBLICA
  return limiteDe(aut.plataforma, aut.canal)
}
// O nome da parte de texto no editor: "Texto" (ou "Texto 2"), e na avaliação
// "Resposta pública (na avaliação)" e "Mensagem no chat".
export function rotuloParte(partes: Pick<ParteAutomacao, 'tipo'>[], i: number): string {
  if (partes[i]?.tipo === 'resposta_publica') return 'Resposta pública (na avaliação)'
  const textos = partes.filter((x) => x.tipo === 'texto').length
  const base = partes.some((x) => x.tipo === 'resposta_publica') ? 'Mensagem no chat' : 'Texto'
  return textos > 1 ? `${base} ${partes.slice(0, i + 1).filter((x) => x.tipo === 'texto').length}` : base
}

// ─── o formulário da regra ──────────────────────────────────────────────────
export interface FormRegra {
  partes: ParteAutomacao[]
  atraso_min: number | string
  dia_todo: boolean
  janela_inicio: string
  janela_fim: string
  condicoes: Record<string, boolean | number | string>
  teto_dia: number | string
}
export function formDaRegra(r: RegraAutomacao): FormRegra {
  return {
    partes: (r.partes || []).map((p) => ({ ...p })),
    atraso_min: r.atraso_min,
    dia_todo: !(r.janela_inicio && r.janela_fim),
    janela_inicio: r.janela_inicio || '09:00',
    janela_fim: r.janela_fim || '20:00',
    condicoes: { ...(r.condicoes || {}) },
    teto_dia: r.teto_dia ?? '',
  }
}
function inteiro(v: unknown): number | null {
  if (v === '' || v === null || v === undefined) return null
  const n = Number(v)
  return Number.isInteger(n) ? n : null
}
function numero(v: unknown): number | null {
  if (v === '' || v === null || v === undefined || typeof v === 'boolean') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
function parteNormal(p: ParteAutomacao): ParteAutomacao {
  if (temTexto(p)) return { tipo: p.tipo, texto: (p.texto || '').trim() }
  if (p.tipo === 'figurinha') return { tipo: 'figurinha', figurinha: p.figurinha ?? null, pacote: p.pacote ?? null }
  return { tipo: p.tipo }
}
export function partesIguais(a: ParteAutomacao[] | null | undefined, b: ParteAutomacao[] | null | undefined): boolean {
  return JSON.stringify((a || []).map(parteNormal)) === JSON.stringify((b || []).map(parteNormal))
}
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/
// O que segura o "salvar" antes de ir à API (a API confere de novo).
export function problemasDoForm(f: FormRegra, aut: Pick<Automacao, 'plataforma' | 'canal' | 'placeholders' | 'condicoes_padrao'>): string[] {
  const out: string[] = []
  const conhecidos = Object.keys(aut.placeholders || {})
  for (const [i, p] of f.partes.entries()) {
    if (!temTexto(p)) continue
    const nome = rotuloParte(f.partes, i)
    const limite = limiteDaParte(aut, p)
    const t = (p.texto || '').trim()
    if (!t) out.push(`${nome}: escreva a mensagem`)
    if (t.length > 2000) out.push(`${nome}: no máximo 2000 caracteres`)
    const fora = placeholdersDesconhecidos(t, conhecidos)
    if (fora.length) {
      const validas = conhecidos.map((c) => `{${c}}`).join(', ') || 'nenhuma'
      out.push(`${nome}: ${fora.map((x) => `{${x}}`).join(', ')} não existe (as lacunas são: ${validas})`)
    }
    if (limite && t && tamanhoComExemplo(t, aut.plataforma) > limite) out.push(`${nome}: passa de ${limite} caracteres (com o nome de exemplo)`)
  }
  const atraso = inteiro(f.atraso_min)
  if (atraso === null || atraso < 0 || atraso > 10080) out.push('Atraso: de 0 a 10080 minutos (7 dias)')
  if (!f.dia_todo) {
    if (!HHMM.test(f.janela_inicio) || !HHMM.test(f.janela_fim)) out.push('Horário: use HH:MM')
    else if (f.janela_inicio >= f.janela_fim) out.push('Horário: o início tem que ser antes do fim (a janela não atravessa a meia-noite)')
  }
  if (f.teto_dia !== '' && f.teto_dia !== null) {
    const teto = inteiro(f.teto_dia)
    if (teto === null || teto < 0 || teto > 6000) out.push('Teto: de 0 a 6000 por dia (vazio = o teto geral)')
  }
  for (const [k, padrao] of Object.entries(aut.condicoes_padrao || {})) {
    if (typeof padrao !== 'number') continue
    const v = numero(f.condicoes[k])
    if (v === null || v <= 0) out.push(`${rotuloCondicao(k)}: um número maior que zero`)
  }
  return out
}
// O corpo do PATCH: só o que mudou (nada mudou = {}).
export function corpoDaRegra(f: FormRegra, regra: RegraAutomacao, aut: Pick<Automacao, 'condicoes_padrao'>): CorpoRegra {
  const corpo: CorpoRegra = {}
  const partes = f.partes.map(parteNormal)
  if (!partesIguais(partes, regra.partes)) corpo.partes = partes
  const atraso = inteiro(f.atraso_min)
  if (atraso !== null && atraso !== regra.atraso_min) corpo.atraso_min = atraso
  const tinha = !!(regra.janela_inicio && regra.janela_fim)
  if (f.dia_todo) {
    if (tinha) corpo.sem_janela = true
  } else if (!tinha || f.janela_inicio !== regra.janela_inicio || f.janela_fim !== regra.janela_fim) {
    corpo.janela_inicio = f.janela_inicio
    corpo.janela_fim = f.janela_fim
  }
  const cond: Record<string, boolean | number> = {}
  for (const [k, v] of Object.entries(f.condicoes)) {
    const padrao = (aut.condicoes_padrao || {})[k]
    const atual = (regra.condicoes || {})[k] ?? padrao
    if (typeof padrao === 'boolean' || typeof atual === 'boolean') {
      if (!!v !== atual) cond[k] = !!v
    } else {
      const n = numero(v)
      if (n !== null && n !== atual) cond[k] = n
    }
  }
  if (Object.keys(cond).length) corpo.condicoes = cond
  const teto = inteiro(f.teto_dia)
  if (f.teto_dia === '' || f.teto_dia === null) {
    if (regra.teto_dia !== null && regra.teto_dia !== undefined) corpo.sem_teto = true
  } else if (teto !== null && teto !== regra.teto_dia) {
    corpo.teto_dia = teto
  }
  return corpo
}

// ─── o registro ─────────────────────────────────────────────────────────────
// O mesmo critério da conta (automacoes_comparar.estatisticas).
export function comparacaoDaLinha(l: Pick<RegistroLinha, 'estado' | 'duoke' | 'divergencia'>): Comparacao | null {
  if (l.divergencia) return 'combinada'
  const manda = ESTADOS_QUE_MANDAM.includes(l.estado)
  if (manda && l.duoke === 'mandou') return 'bateu'
  if (l.estado === 'pulado' && l.duoke === 'nao_mandou') return 'bateu'
  if (manda && l.duoke === 'nao_mandou') return 'so_davinci'
  if ((l.estado === 'pulado' && l.duoke === 'mandou') || l.estado === 'so_duoke') return 'so_duoke'
  if (l.duoke === 'pendente' && l.estado !== 'agendado') return 'pendente'
  return null
}
export function comparacaoInfo(l: Pick<RegistroLinha, 'estado' | 'duoke' | 'divergencia'>): { label: string; hint: string; cls: string } | null {
  const c = comparacaoDaLinha(l)
  return c ? COMPARACOES[c] : null
}
export function estadoDaLinha(l: Pick<RegistroLinha, 'estado' | 'modo' | 'motivo'>): { label: string; hint: string; cls: string } {
  const base = ESTADOS_REGISTRO[l.estado] ?? { label: l.estado, hint: '', cls: CLS_CINZA }
  if (l.estado === 'simulado' && l.motivo === 'envio_desligado') {
    return { label: 'não saiu', hint: 'regra em Enviar com a chave de envio desligada: nada saiu', cls: CLS_VERMELHO }
  }
  if (l.estado === 'pulado' && l.modo === 'enviar') return { ...base, label: 'não mandou' }
  return base
}
export function alvoDaLinha(l: Pick<RegistroLinha, 'alvo' | 'pedido'>): string {
  const alvo = ALVOS[l.alvo] || l.alvo
  if (l.pedido) return l.alvo === 'pedido' ? `pedido ${l.pedido}` : `${alvo} · pedido ${l.pedido}`
  return alvo
}
export interface FiltrosRegistro {
  automacao: string
  integration_id: string
  estado: string
  comparacao: string
}
export function filtrosRegistroVazios(): FiltrosRegistro {
  return { automacao: '', integration_id: '', estado: '', comparacao: '' }
}
export function paramsRegistro(f: FiltrosRegistro, antes?: string | null): string {
  const p = new URLSearchParams()
  if (f.automacao) p.set('automacao', f.automacao)
  if (f.integration_id) p.set('integration_id', f.integration_id)
  if (f.estado) p.set('estado', f.estado)
  if (f.comparacao.startsWith('duoke:')) p.set('duoke', f.comparacao.slice('duoke:'.length))
  else if (f.comparacao) p.set('so', f.comparacao)
  p.set('limite', String(LIMITE_REGISTRO))
  if (antes) p.set('antes', antes)
  return p.toString()
}

// ─── a prévia da linha ("como o cliente receberia") ────────────────────────
// O nome de cada parte (no balão e no title).
export const PARTES_VISTA: Record<string, string> = {
  texto: 'texto',
  cartao_pedido: 'cartão do pedido',
  figurinha: 'figurinha',
  resposta_publica: 'resposta pública (na avaliação)',
  outro: 'outra mensagem',
}
// A hora da nossa: a que saiu, a que sairia (a decisão do modo seco) ou a prevista.
export const HORA_DAVINCI: Record<string, string> = { saiu: 'saiu', sairia: 'sairia', devido: 'prevista para' }
// O texto da regra mudou depois que a linha foi decidida (a prévia é a de agora).
export function versaoMudou(d: Pick<PreviaDavinci, 'versao_regra' | 'versao_da_linha'>): boolean {
  return d.versao_regra !== null && d.versao_da_linha !== null && d.versao_regra !== d.versao_da_linha
}
// O lado do Duoke sem mensagem: por quê.
export function semDuoke(d: Pick<PreviaDuoke, 'estado' | 'janela_de' | 'janela_ate'>, fmt: (v: string) => string): string {
  const janela = d.janela_de && d.janela_ate ? ` (procurado de ${fmt(d.janela_de)} a ${fmt(d.janela_ate)})` : ''
  if (d.estado === 'nao_mandou') return `O Duoke não mandou${janela}.`
  if (d.estado === 'pendente') return `Ainda conferindo: a leitura da loja não passou da janela${janela}.`
  if (d.estado === 'mandou') return 'O Duoke mandou, mas a mensagem não está mais na caixa do DaVinci.'
  return 'Não se compara com o Duoke.'
}
// O DaVinci não mandaria (ou não mandou): o porquê, em português.
export function porQueNaoSairia(d: Pick<PreviaDavinci, 'sairia' | 'estado' | 'motivo' | 'motivo_texto'>): string | null {
  if (d.sairia) return null
  if (d.estado === 'so_duoke') return 'O DaVinci não tinha esta linha: só o Duoke mandou.'
  return `Não sairia: ${d.motivo_texto || d.motivo || ESTADOS_REGISTRO[d.estado]?.hint || d.estado}. Abaixo, o que sairia se a regra deixasse.`
}

// ─── erros ──────────────────────────────────────────────────────────────────
// O `detail` das rotas da aba vem com `code` e, conforme o caso, `motivos`
// (o 409: a lista toda; o 422 do texto: os do validador), `condicoes` ou
// `condicao`. O resto (rede, 403, pydantic) é o erroDaApi de sempre.
export function erroDaAutomacao(e: any, padrao = 'Algo deu errado'): { texto: string; motivos: string[] } {
  const d = e?.data?.detail
  if (d && typeof d === 'object' && !Array.isArray(d) && typeof d.code === 'string' && (ERROS_AUTOMACAO[d.code] || Array.isArray(d.motivos))) {
    const motivos: string[] = []
    if (Array.isArray(d.motivos)) {
      for (const m of d.motivos) {
        if (m === d.code) continue
        motivos.push(POR_QUE_NAO_ENVIAR[String(m)] || String(m))
      }
    }
    if (Array.isArray(d.condicoes)) motivos.push(...d.condicoes.map((c: unknown) => rotuloCondicao(String(c))))
    if (typeof d.condicao === 'string') motivos.push(rotuloCondicao(d.condicao))
    return { texto: ERROS_AUTOMACAO[d.code] || (typeof d.detail === 'string' && d.detail) || padrao, motivos }
  }
  return erroDaApi(e, padrao)
}
</script>

<script setup lang="ts">
import {
  Check,
  ChevronRight,
  ExternalLink,
  Eye,
  Info,
  Loader2,
  Lock,
  Pencil,
  RotateCcw,
  Save,
  Search,
  ShieldAlert,
  TriangleAlert,
  Undo2,
  X,
  Zap,
} from 'lucide-vue-next'
import { fmtDataHora, haQuanto, useRelogio, usePollingVisivel } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ canEdit: boolean }>()
const emit = defineEmits<{ (e: 'abrir-conversa', id: string): void }>()

const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

// ─── plataforma e período (lembrados neste navegador) ──────────────────────
const PREF_KEY = 'davinci.atendimento.automaticas'
const plataforma = ref<PlataformaAutomacao>('shopee')
const dias = ref(7)
const busca = ref('')
const mostrar = ref<Mostrar>('todas')
function salvarPreferencias() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({ plataforma: plataforma.value, dias: dias.value }))
  } catch {
    // sem localStorage — só não lembra
  }
}
function lerPreferencias() {
  try {
    const salvo = JSON.parse(localStorage.getItem(PREF_KEY) || 'null')
    if (salvo && PLATAFORMAS_AUTOMACAO.some((p) => p.value === salvo.plataforma)) plataforma.value = salvo.plataforma
    if (salvo && PERIODOS.includes(Number(salvo.dias))) dias.value = Number(salvo.dias)
  } catch {
    // sem localStorage (ou lixo salvo) — começa no padrão
  }
}

// ─── o catálogo × as lojas ──────────────────────────────────────────────────
const dados = ref<AutomacoesResposta | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
// Resposta que chega depois de trocar de plataforma não sobrescreve a nova.
let geracao = 0
async function carregar(silencioso = false) {
  const g = ++geracao
  if (!silencioso) carregando.value = true
  try {
    const r = await api<AutomacoesResposta>(`/api/atendimento/automacoes?plataforma=${encodeURIComponent(plataforma.value)}&dias=${dias.value}`)
    if (g !== geracao) return
    dados.value = r
    erro.value = null
  } catch (e: any) {
    if (g !== geracao) return
    // No automático, falha não apaga o que está na tela.
    if (!silencioso || !dados.value) erro.value = erroDaApi(e, 'Não consegui carregar as automáticas').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
function trocarPlataforma(p: PlataformaAutomacao) {
  if (p === plataforma.value) return
  plataforma.value = p
  dados.value = null
  abertas.value = []
  fecharEditor()
  pedindoEnviar.value = null
  fecharPreviaDaLinha()
  salvarPreferencias()
  // O filtro do registro era de outra plataforma.
  Object.assign(filtrosRegistro, { automacao: '', integration_id: '' })
  void carregar()
}
function trocarDias(d: number) {
  if (d === dias.value) return
  dias.value = d
  salvarPreferencias()
  void carregar()
}
function atualizar() {
  void carregar()
  void carregarRegistro()
}

const automacoes = computed(() => (dados.value?.automacoes || []).filter((a) => a.plataforma === plataforma.value))
const semLojas = computed(() => !!dados.value && automacoes.value.every((a) => !a.lojas.length))
const chips = computed(() => chipsDasChaves(dados.value?.chaves, plataforma.value))
const periodo = computed(() => `${dias.value} dias`)
const textosMotivos = computed(() => dados.value?.motivos || {})
// O "Enviar" travado pelo mesmo motivo em todas: a linha de cima diz uma vez.
const travaGeral = computed(() => {
  const ch = dados.value?.chaves
  if (!ch) return null
  if (!ch.envio_automacoes) return POR_QUE_NAO_ENVIAR.envio_desligado
  if (!ch.envio_geral) return POR_QUE_NAO_ENVIAR.envio_geral_desligado
  return null
})

// Seções abertas (fechadas por padrão: cada automação × ~20 lojas). Ao começar
// a buscar ou a filtrar, todas abrem (e continuam podendo fechar).
const abertas = ref<string[]>([])
function estaAberta(codigo: string) {
  return abertas.value.includes(codigo)
}
watch(() => !!busca.value.trim() || mostrar.value !== 'todas', (ativo, antes) => {
  if (ativo && !antes) abertas.value = automacoes.value.map((a) => a.codigo)
})
function alternarAutomacao(codigo: string) {
  abertas.value = abertas.value.includes(codigo) ? abertas.value.filter((c) => c !== codigo) : [...abertas.value, codigo]
}
const todasAbertas = computed(() => automacoes.value.length > 0 && automacoes.value.every((a) => abertas.value.includes(a.codigo)))
function alternarTodas() {
  abertas.value = todasAbertas.value ? [] : automacoes.value.map((a) => a.codigo)
}
function lojasDe(aut: Automacao) {
  return lojasVisiveis(aut.lojas, busca.value, mostrar.value)
}

function trocarLoja(codigo: string, integrationId: string, mudou: Partial<LojaAutomacao>) {
  const aut = dados.value?.automacoes.find((a) => a.codigo === codigo)
  const loja = aut?.lojas.find((l) => l.integration_id === integrationId)
  if (loja) Object.assign(loja, mudou)
}

// ─── modo ───────────────────────────────────────────────────────────────────
const salvandoModo = ref<string | null>(null)
// A confirmação de "Enviar" (a linha abre o quadro com o "desliguei no Duoke"
// e, se o critério da troca não passou NESTA loja, o "sei disso").
const pedindoEnviar = ref<string | null>(null)
const desligueiNoDuoke = ref(false)
const trocaSemCriterio = ref(false)
// A API recusou pelo critério (a lista estava velha): o quadro pede o "sei disso".
const criterioFalhou = ref<string | null>(null)
let ultimoErro: string | null = null
function pedeCriterio(aut: Automacao, loja: LojaAutomacao): boolean {
  return !loja.pode_trocar || criterioFalhou.value === chaveLoja(aut, loja)
}

async function mudarModo(aut: Automacao, loja: LojaAutomacao, novo: ModoAutomacao, extra: CorpoRegra = {}) {
  const k = chaveLoja(aut, loja)
  salvandoModo.value = k
  ultimoErro = null
  try {
    const r = await api<RegraMudou>(`/api/atendimento/automacoes/${encodeURIComponent(aut.codigo)}/${encodeURIComponent(loja.integration_id)}`, {
      method: 'PATCH',
      body: { modo: novo, ...extra },
    })
    trocarLoja(aut.codigo, loja.integration_id, { regra: r.regra, pode_enviar: r.pode_enviar, por_que_nao_enviar: r.por_que_nao_enviar })
    const m = modoAutomacaoInfo(novo)
    const detalhe = [m.hint]
    if (r.rearmadas) detalhe.push(`${r.rearmadas} que o Duoke não mandou voltaram para a fila (o DaVinci confere de novo antes de mandar).`)
    toasts.success(`${aut.nome} · ${loja.loja}: ${m.label}`, detalhe)
    // Uma atualização automática que saiu antes do PATCH traria o modo antigo:
    // a releitura depois dele vence (geração mais nova).
    void carregar(true)
    return true
  } catch (e: any) {
    const er = erroDaAutomacao(e, 'Não consegui mudar o modo')
    ultimoErro = typeof e?.data?.detail?.code === 'string' ? e.data.detail.code : null
    toasts.error(er.texto, er.motivos)
    return false
  } finally {
    salvandoModo.value = null
  }
}

function aoEscolherModo(aut: Automacao, loja: LojaAutomacao, ev: Event) {
  const sel = ev.target as HTMLSelectElement
  const novo = sel.value as ModoAutomacao
  const atual = loja.regra.modo
  // O seletor volta ao modo de agora: quem muda é a resposta da API.
  sel.value = atual
  if (novo === atual) return
  if (novo === 'enviar') {
    // A opção já vem desabilitada; isto é a segunda trava (a API é a terceira).
    if (!loja.pode_enviar) {
      toasts.warning('Enviar está travado nesta loja', motivosEnviar(loja.por_que_nao_enviar))
      return
    }
    pedindoEnviar.value = chaveLoja(aut, loja)
    desligueiNoDuoke.value = false
    trocaSemCriterio.value = false
    criterioFalhou.value = null
    return
  }
  if (atual === 'enviar' && !confirm(`Tirar "${aut.nome}" de ENVIAR em ${loja.loja}?\n\nO DaVinci para de mandar esta mensagem nesta loja. Se ela já foi desligada no Duoke, o comprador fica sem — religue no Duoke antes.`)) return
  void mudarModo(aut, loja, novo)
}
async function confirmarEnviar(aut: Automacao, loja: LojaAutomacao) {
  if (!desligueiNoDuoke.value) return
  const semCriterio = pedeCriterio(aut, loja)
  if (semCriterio && !trocaSemCriterio.value) return
  const ok = await mudarModo(aut, loja, 'enviar', semCriterio ? { desliguei_no_duoke: true, troca_sem_criterio: true } : { desliguei_no_duoke: true })
  if (ok) {
    pedindoEnviar.value = null
    criterioFalhou.value = null
  } else if (ultimoErro === 'criterio_nao_passou') {
    criterioFalhou.value = chaveLoja(aut, loja)
    trocaSemCriterio.value = false
  }
}

// ─── simular nas lojas do Duoke ─────────────────────────────────────────────
const simulando = ref<string | null>(null)
async function simularNasLojasDoDuoke(aut: Automacao) {
  const n = faltaSimularNoDuoke(aut.lojas)
  if (!n || simulando.value) return
  if (!confirm(`Ligar "${aut.nome}" em SIMULAR nas ${n} loja(s) onde o Duoke manda hoje e que estão desligadas aqui?\n\nModo seco: o DaVinci só registra o que mandaria e compara com o Duoke. Nada é enviado. Regra que já está em Simular ou Enviar não muda.`)) return
  simulando.value = aut.codigo
  try {
    const r = await api<SimularNasLojas>(`/api/atendimento/automacoes/${encodeURIComponent(aut.codigo)}/simular-nas-lojas-do-duoke`, { method: 'POST' })
    toasts.success(`${aut.nome}: em simulação nas lojas do Duoke`, [`${r.criadas} criada(s), ${r.ligadas} ligada(s), ${r.mantidas} já estavam ligadas.`])
    await carregar(true)
  } catch (e: any) {
    const er = erroDaAutomacao(e, 'Não consegui ligar a simulação')
    toasts.error(er.texto, er.motivos)
  } finally {
    simulando.value = null
  }
}

// ─── o editor da regra (uma loja por vez) ───────────────────────────────────
const editando = ref<string | null>(null)
const form = reactive<FormRegra>({ partes: [], atraso_min: 0, dia_todo: true, janela_inicio: '09:00', janela_fim: '20:00', condicoes: {}, teto_dia: '' })
const salvandoRegra = ref(false)
const erroSalvar = ref<{ texto: string; motivos: string[] } | null>(null)
const previa = ref<PreviaResposta | null>(null)
const previaErro = ref<string | null>(null)
const previaCarregando = ref(false)
const caixas: (HTMLTextAreaElement | null)[] = []
let focoCaixa = 0
function guardarCaixa(i: number, el: unknown) {
  caixas[i] = (el as HTMLTextAreaElement | null) ?? null
}

const editorAut = computed(() => automacoes.value.find((a) => editando.value?.startsWith(`${a.codigo}:`)) || null)
const editorLoja = computed(() => {
  const a = editorAut.value
  return a ? a.lojas.find((l) => chaveLoja(a, l) === editando.value) || null : null
})
const corpo = computed<CorpoRegra>(() => (editorAut.value && editorLoja.value ? corpoDaRegra(form, editorLoja.value.regra, editorAut.value) : {}))
const mudou = computed(() => Object.keys(corpo.value).length > 0)
const problemas = computed(() => (editorAut.value ? problemasDoForm(form, editorAut.value) : []))
const condicoesDoEditor = computed(() => Object.entries(editorAut.value?.condicoes_padrao || {}).map(([chave, padrao]) => ({ chave, padrao, ...(CONDICOES[chave] || { label: chave }) })))

function abrirEditor(aut: Automacao, loja: LojaAutomacao) {
  const k = chaveLoja(aut, loja)
  if (editando.value === k) return fecharEditor()
  if (editando.value && mudou.value && !confirm('Descartar o que você mudou na outra regra?')) return
  editando.value = k
  Object.assign(form, formDaRegra(loja.regra))
  erroSalvar.value = null
  previa.value = null
  previaErro.value = null
  caixas.length = 0
  focoCaixa = 0
  void carregarPrevia()
}
function fecharEditor() {
  editando.value = null
  previa.value = null
  erroSalvar.value = null
  if (tPrevia) clearTimeout(tPrevia)
}
function desfazer() {
  const l = editorLoja.value
  if (!l) return
  Object.assign(form, formDaRegra(l.regra))
  erroSalvar.value = null
}
function inserirPlaceholder(nome: string) {
  const i = form.partes.findIndex((p, j) => p.tipo === 'texto' && j === focoCaixa)
  const alvo = i >= 0 ? i : form.partes.findIndex((p) => p.tipo === 'texto')
  if (alvo < 0) return
  const p = form.partes[alvo]
  const el = caixas[alvo]
  const t = `{${nome}}`
  const texto = p.texto || ''
  const ini = el?.selectionStart ?? texto.length
  const fim = el?.selectionEnd ?? ini
  p.texto = texto.slice(0, ini) + t + texto.slice(fim)
  void nextTick(() => {
    el?.focus()
    el?.setSelectionRange(ini + t.length, ini + t.length)
  })
}

// A prévia: o backend renderiza com o nome de exemplo e passa no validador
// (nada é enviado). Espera a pessoa parar de digitar.
let tPrevia: ReturnType<typeof setTimeout> | null = null
let geracaoPrevia = 0
async function carregarPrevia() {
  const aut = editorAut.value
  const loja = editorLoja.value
  if (!aut || !loja) return
  const g = ++geracaoPrevia
  previaCarregando.value = true
  try {
    const r = await api<PreviaResposta>('/api/atendimento/automacoes/previa', {
      method: 'POST',
      body: { automacao: aut.codigo, integration_id: loja.integration_id, partes: form.partes.map((p) => (p.tipo === 'texto' ? { tipo: 'texto', texto: (p.texto || '').trim() } : { ...p })) },
    })
    if (g !== geracaoPrevia) return
    previa.value = r
    previaErro.value = null
  } catch (e: any) {
    if (g !== geracaoPrevia) return
    previa.value = null
    const er = erroDaAutomacao(e, 'Não consegui montar a prévia')
    previaErro.value = [er.texto, ...er.motivos].join(' — ')
  } finally {
    if (g === geracaoPrevia) previaCarregando.value = false
  }
}
// Só o texto mudando NA MESMA regra (abrir outra já pede a prévia na hora).
watch(() => [editando.value, JSON.stringify(form.partes)] as const, ([ed, partes], [edAntes, partesAntes]) => {
  if (!ed || ed !== edAntes || partes === partesAntes) return
  if (tPrevia) clearTimeout(tPrevia)
  tPrevia = setTimeout(() => void carregarPrevia(), 500)
})
const semNomeDiferente = computed(() => !!previa.value && !partesIguais(previa.value.partes, previa.value.sem_nome))

async function salvarRegra() {
  const aut = editorAut.value
  const loja = editorLoja.value
  if (!aut || !loja || !props.canEdit || salvandoRegra.value || !mudou.value || problemas.value.length) return
  salvandoRegra.value = true
  erroSalvar.value = null
  try {
    const r = await api<RegraMudou>(`/api/atendimento/automacoes/${encodeURIComponent(aut.codigo)}/${encodeURIComponent(loja.integration_id)}`, {
      method: 'PATCH',
      body: corpo.value,
    })
    trocarLoja(aut.codigo, loja.integration_id, { regra: r.regra, pode_enviar: r.pode_enviar, por_que_nao_enviar: r.por_que_nao_enviar })
    Object.assign(form, formDaRegra(r.regra))
    toasts.success(`${aut.nome} · ${loja.loja}: regra salva`, r.regra.versao ? [`versão ${r.regra.versao}`] : undefined)
    void carregar(true)
  } catch (e: any) {
    erroSalvar.value = erroDaAutomacao(e, 'Não consegui salvar a regra')
  } finally {
    salvandoRegra.value = false
  }
}

// ─── o registro recente ─────────────────────────────────────────────────────
const registroEl = ref<HTMLElement | null>(null)
const filtrosRegistro = reactive<FiltrosRegistro>(filtrosRegistroVazios())
const registro = ref<RegistroLinha[]>([])
const registroProximo = ref<string | null>(null)
const registroCarregando = ref(false)
const registroMais = ref(false)
const registroErro = ref<string | null>(null)
let geracaoRegistro = 0
const filtrandoRegistro = computed(() => Object.values(filtrosRegistro).some(Boolean))
const lojasDoRegistro = computed(() => {
  const vistas = new Map<string, string>()
  for (const a of automacoes.value) for (const l of a.lojas) vistas.set(l.integration_id, l.loja)
  return [...vistas.entries()].map(([id, nome]) => ({ id, nome })).sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
})

async function carregarRegistro() {
  const g = ++geracaoRegistro
  registroCarregando.value = true
  registroErro.value = null
  try {
    const r = await api<RegistroResposta>(`/api/atendimento/automacoes/registro?${paramsRegistro(filtrosRegistro)}`)
    if (g !== geracaoRegistro) return
    registro.value = r.linhas || []
    registroProximo.value = r.proximo || null
  } catch (e: any) {
    if (g !== geracaoRegistro) return
    registroErro.value = erroDaApi(e, 'Não consegui carregar o registro').texto
  } finally {
    if (g === geracaoRegistro) registroCarregando.value = false
  }
}
async function carregarMaisRegistro() {
  if (!registroProximo.value || registroMais.value) return
  const g = geracaoRegistro
  registroMais.value = true
  try {
    const r = await api<RegistroResposta>(`/api/atendimento/automacoes/registro?${paramsRegistro(filtrosRegistro, registroProximo.value)}`)
    if (g !== geracaoRegistro) return
    const ids = new Set(registro.value.map((l) => l.id))
    registro.value = [...registro.value, ...(r.linhas || []).filter((l) => !ids.has(l.id))]
    registroProximo.value = r.proximo || null
  } catch (e: any) {
    registroErro.value = erroDaApi(e, 'Não consegui carregar mais do registro').texto
  } finally {
    registroMais.value = false
  }
}
watch(filtrosRegistro, () => void carregarRegistro(), { deep: true })
function limparFiltrosRegistro() {
  Object.assign(filtrosRegistro, filtrosRegistroVazios())
}
// "só DaVinci"/"só Duoke" da linha da loja: o registro filtrado, e a tela vai até ele.
function verNoRegistro(aut: Automacao, loja: LojaAutomacao | null, comparacao: string) {
  Object.assign(filtrosRegistro, { automacao: aut.codigo, integration_id: loja?.integration_id || '', estado: '', comparacao })
  void nextTick(() => {
    try {
      registroEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } catch {
      // navegador sem scrollIntoView suave — fica onde está
    }
  })
}
function abrirConversa(id: string | null) {
  if (id) emit('abrir-conversa', id)
}

// ─── a prévia de uma linha: "como o cliente receberia" × o Duoke ───────────
// Só quando a pessoa abre (o registro em si não tem texto): a API monta na
// hora as partes que sairiam e mostra o que o Duoke mandou de verdade.
const previaAberta = ref<string | null>(null)
const previaLinha = ref<PreviaRegistro | null>(null)
const previaLinhaErro = ref<string | null>(null)
const previaLinhaCarregando = ref(false)
let geracaoPreviaLinha = 0
function fecharPreviaDaLinha() {
  geracaoPreviaLinha++
  previaAberta.value = null
  previaLinha.value = null
  previaLinhaErro.value = null
  previaLinhaCarregando.value = false
}
async function abrirPreviaDaLinha(l: RegistroLinha) {
  if (previaAberta.value === l.id) return fecharPreviaDaLinha()
  const g = ++geracaoPreviaLinha
  previaAberta.value = l.id
  previaLinha.value = null
  previaLinhaErro.value = null
  previaLinhaCarregando.value = true
  try {
    const r = await api<PreviaRegistro>(`/api/atendimento/automacoes/registro/${encodeURIComponent(l.id)}/previa`)
    if (g !== geracaoPreviaLinha) return
    previaLinha.value = r
  } catch (e: any) {
    if (g !== geracaoPreviaLinha) return
    previaLinhaErro.value = erroDaAutomacao(e, 'Não consegui montar a prévia desta linha').texto
  } finally {
    if (g === geracaoPreviaLinha) previaLinhaCarregando.value = false
  }
}

// ─── atualização ────────────────────────────────────────────────────────────
// O motor roda a cada 2 min: a conta se atualiza sozinha a cada 1 min (só com
// a aba visível). O registro, no "atualizar" e nos filtros (a página que a
// pessoa está lendo não pula).
usePollingVisivel(() => carregar(true), 60_000)
onMounted(() => {
  lerPreferencias()
  void carregar()
  void carregarRegistro()
})
</script>

<template>
  <!-- color-scheme herda: hora, número, seletor e caixa de marcar no tema escuro -->
  <div class="space-y-4 dark:[color-scheme:dark]" data-automaticas>
    <!-- a faixa: o que o servidor deixa (o texto vem da API) e as chaves -->
    <div
      v-if="dados?.faixa"
      role="status"
      class="rounded-md border px-3 py-2 text-xs"
      :class="FAIXA_CLS[dados.faixa.nivel] || FAIXA_CLS.info"
      data-faixa-automaticas
      :data-nivel="dados.faixa.nivel"
    >
      <div class="flex items-start gap-2">
        <TriangleAlert v-if="dados.faixa.nivel !== 'info'" class="mt-px size-4 shrink-0" />
        <Info v-else class="mt-px size-4 shrink-0" />
        <div class="min-w-0 flex-1 space-y-1.5">
          <p class="font-medium">{{ dados.faixa.texto }}</p>
          <ul class="flex flex-wrap gap-1.5" aria-label="chaves do servidor">
            <li
              v-for="c in chips"
              :key="c.chave"
              class="rounded border bg-background/60 px-1.5 py-px text-[11px]"
              :class="TOM_CHIP[c.tom]"
              :title="c.hint"
              :data-chave="c.chave"
            >{{ c.texto }}</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- plataforma, período, busca -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex gap-1 rounded-md bg-muted/40 p-1" role="tablist" aria-label="plataforma">
        <button
          v-for="p in PLATAFORMAS_AUTOMACAO"
          :key="p.value"
          type="button"
          role="tab"
          :aria-selected="plataforma === p.value"
          class="rounded px-2.5 py-1 text-xs transition-colors"
          :class="plataforma === p.value ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          @click="trocarPlataforma(p.value)"
        >{{ p.nome }}</button>
      </div>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1" aria-label="período da conta">
        <button
          v-for="d in PERIODOS"
          :key="d"
          type="button"
          :aria-pressed="dias === d"
          class="rounded px-2.5 py-1 text-xs transition-colors"
          :class="dias === d ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          @click="trocarDias(d)"
        >{{ d }} dias</button>
      </div>
      <div class="relative w-full sm:w-52">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input v-model="busca" class="h-9 w-full rounded-md border bg-background pl-8 pr-3 text-sm" placeholder="buscar loja…" aria-label="buscar loja" />
      </div>
      <select v-model="mostrar" class="h-9 w-full rounded-md border bg-background px-2 text-sm sm:w-auto" aria-label="quais lojas mostrar">
        <option v-for="m in MOSTRAR" :key="m.value" :value="m.value">{{ m.label }}</option>
      </select>
      <div class="flex w-full items-center gap-2 sm:ml-auto sm:w-auto">
        <Button size="sm" variant="ghost" class="text-xs" :disabled="!automacoes.length" @click="alternarTodas">{{ todasAbertas ? 'fechar todas' : 'abrir todas' }}</Button>
        <Button size="sm" variant="outline" class="ml-auto sm:ml-0" :disabled="carregando" @click="atualizar">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
      </div>
    </div>

    <p v-if="travaGeral" class="flex items-start gap-1.5 text-xs text-muted-foreground" data-trava-geral>
      <Lock class="mt-px size-3.5 shrink-0" />
      <span><span class="font-medium text-foreground">Enviar está travado em todas as lojas:</span> {{ travaGeral }}. Simular não manda nada — é para comparar com o Duoke.</span>
    </p>

    <!-- carregando / erro / vazio -->
    <div v-if="carregando && !dados" class="space-y-2" aria-busy="true" data-carregando>
      <div class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando as automáticas…</div>
      <div v-for="i in 3" :key="i" class="h-20 animate-pulse rounded-lg border bg-muted/30" />
    </div>
    <div v-else-if="erro && !dados" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300" role="alert" data-erro>
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar()">tentar de novo</button>
    </div>
    <EmptyState
      v-else-if="semLojas"
      :icon="Zap"
      :title="`Nenhuma loja ${PLATAFORMAS_AUTOMACAO.find((p) => p.value === plataforma)?.nome || ''} na sua equipe`"
      description="As automáticas aparecem por loja integrada (e não arquivada). Confira em Integrações."
      data-vazio
    />

    <div v-if="erro && dados" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs text-red-700 dark:text-red-300" role="alert">
      {{ erro }} — mostrando o que já estava na tela.
    </div>

    <!-- uma seção por automação -->
    <template v-if="dados && !semLojas">
      <section
        v-for="aut in automacoes"
        :key="aut.codigo"
        class="overflow-hidden rounded-lg border bg-card"
        :data-automacao="aut.codigo"
      >
        <button
          type="button"
          class="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-muted/30"
          :aria-expanded="estaAberta(aut.codigo)"
          :aria-controls="`automacao-${aut.codigo}`"
          @click="alternarAutomacao(aut.codigo)"
        >
          <ChevronRight class="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform" :class="estaAberta(aut.codigo) ? 'rotate-90' : ''" />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-sm font-medium">{{ aut.nome }}</span>
              <span class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" title="o que dispara">{{ gatilhoLegivel(aut, automacoes) }}</span>
              <span class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" title="quanto espera depois do gatilho (padrão do catálogo)">{{ fmtAtraso(aut.atraso_min) }}</span>
              <span class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" title="horário de Brasília (padrão do catálogo)">{{ janelaLegivel(aut.janela_inicio, aut.janela_fim) }}</span>
              <span v-if="aut.campanha" class="rounded bg-orange-500/15 px-1.5 py-px text-[11px] text-orange-800 dark:text-orange-300" title="campanha da Shopee: hoje o Duoke manda como resposta automática (auto_reply)">campanha</span>
              <span v-if="aut.diferenca_combinada" class="rounded bg-violet-500/15 px-1.5 py-px text-[11px] text-violet-700 dark:text-violet-300" :title="SELOS_COMBINADA[aut.diferenca_combinada] || dados.divergencias[aut.diferenca_combinada] || aut.diferenca_combinada" data-selo-combinada>diferença combinada</span>
              <span v-if="aut.travada" class="rounded bg-red-500/15 px-1.5 py-px text-[11px] text-red-700 dark:text-red-300" :title="POR_QUE_NAO_ENVIAR.sem_texto">sem texto</span>
              <span v-if="aut.so_simulacao" class="rounded bg-violet-500/15 px-1.5 py-px text-[11px] text-violet-700 dark:text-violet-300" :title="`Só simulação: ${aut.so_simulacao_texto || aut.so_simulacao}`" data-so-simulacao>só simulação</span>
            </div>
            <p class="mt-0.5 text-xs text-muted-foreground">{{ aut.descricao }}</p>
          </div>
        </button>

        <!-- os totais da automação (todas as lojas) -->
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-3 py-1.5 text-xs" data-totais>
          <span><span class="text-muted-foreground">24 h:</span> <span class="font-semibold tabular-nums">{{ mandaria(aut.total_24h) }}</span> <span class="text-muted-foreground">mandaria</span></span>
          <span><span class="text-muted-foreground">{{ periodo }}:</span> <span class="font-semibold tabular-nums">{{ mandaria(aut.total_periodo) }}</span></span>
          <span class="cursor-help" :title="tituloConta(aut.total_periodo, aut.tipo, periodo, textosMotivos)">
            <span class="text-muted-foreground">bateu com o Duoke:</span> <span class="tabular-nums" :class="clsPct(bateuDe(aut.total_periodo, aut.tipo).valor)">{{ fmtPct(bateuDe(aut.total_periodo, aut.tipo).valor) }}</span>
          </span>
          <button
            v-if="aut.total_periodo?.so_davinci"
            type="button"
            class="text-amber-800 underline decoration-dotted underline-offset-2 dark:text-amber-300"
            title="ver no registro: o DaVinci mandaria e o Duoke não mandou"
            @click="verNoRegistro(aut, null, 'so_davinci')"
          >só DaVinci {{ aut.total_periodo.so_davinci }}</button>
          <button
            v-if="aut.total_periodo?.so_duoke"
            type="button"
            class="text-amber-800 underline decoration-dotted underline-offset-2 dark:text-amber-300"
            title="ver no registro: o Duoke mandou e o DaVinci não mandaria"
            @click="verNoRegistro(aut, null, 'so_duoke')"
          >só Duoke {{ aut.total_periodo.so_duoke }}</button>
          <button
            v-if="aut.total_periodo?.alertas"
            type="button"
            class="inline-flex items-center gap-1 rounded bg-red-500/15 px-1.5 py-px font-medium text-red-700 dark:text-red-300"
            title="tolerância zero: mandaria para quem devolveu, cancelou ou reclamou — trava a troca"
            @click="verNoRegistro(aut, null, 'alerta')"
          ><ShieldAlert class="size-3" /> {{ aut.total_periodo.alertas }} alerta(s)</button>
          <span
            v-if="lojasProntas(aut.lojas).ligadas"
            class="rounded px-1.5 py-px text-[11px]"
            :class="lojasProntas(aut.lojas).prontas ? CLS_TROCA_PRONTA : 'bg-muted text-muted-foreground'"
            :title="`A troca é loja por loja: o critério de cada loja nos últimos 7 dias (o selo na linha dela). A soma das lojas é só informação${aut.total_periodo ? ` — ${aut.total_periodo.pode_trocar ? 'passaria' : 'não passaria'} no critério` : ''}.`"
            data-troca-automacao
          >{{ lojasProntas(aut.lojas).prontas }} de {{ lojasProntas(aut.lojas).ligadas }} {{ lojasProntas(aut.lojas).ligadas === 1 ? 'loja pronta' : 'lojas prontas' }} para trocar</span>
          <span class="text-muted-foreground sm:ml-auto">
            <template v-for="(n, modo, i) in contarModos(aut.lojas)" :key="modo"><template v-if="i"> · </template>{{ n }} {{ modoAutomacaoInfo(modo).label.toLowerCase() }}</template>
          </span>
          <Button
            v-if="canEdit && !aut.travada"
            size="sm"
            variant="outline"
            class="h-7 text-xs"
            :disabled="!faltaSimularNoDuoke(aut.lojas) || simulando === aut.codigo"
            :title="faltaSimularNoDuoke(aut.lojas) ? 'ligar em Simular (modo seco) nas lojas onde o Duoke manda hoje e que estão desligadas' : 'todas as lojas do Duoke já estão ligadas'"
            data-simular-duoke
            @click="simularNasLojasDoDuoke(aut)"
          >
            <Loader2 v-if="simulando === aut.codigo" class="mr-1 size-3.5 animate-spin" />
            simular nas lojas do Duoke
          </Button>
        </div>

        <!-- as lojas -->
        <div v-if="estaAberta(aut.codigo)" :id="`automacao-${aut.codigo}`" class="border-t text-xs">
          <div class="hidden gap-x-3 bg-muted/40 px-3 py-1.5 text-[11px] font-semibold text-muted-foreground md:grid" :class="GRADE_LOJA">
            <span>Loja</span>
            <span>Modo</span>
            <span class="text-right" title="quantas mandaria nas últimas 24 h">24 h</span>
            <span class="text-right" :title="`quantas mandaria em ${periodo}`">{{ periodo }}</span>
            <span class="text-right" title="a menor entre precisão e cobertura com o Duoke (nas opções, a cobertura); verde a partir de 95%">bateu</span>
            <span class="text-right" title="o DaVinci mandaria e o Duoke não mandou">só DaVinci</span>
            <span class="text-right" title="o Duoke mandou e o DaVinci não mandaria">só Duoke</span>
            <span>Último</span>
          </div>
          <p v-if="!lojasDe(aut).length" class="px-3 py-4 text-center text-muted-foreground">Nenhuma loja com esse filtro.</p>
          <div v-for="loja in lojasDe(aut)" :key="loja.integration_id" class="border-b last:border-b-0" :data-loja="loja.integration_id">
            <div class="grid grid-cols-3 items-center gap-x-3 gap-y-1.5 px-3 py-2 md:gap-y-0" :class="GRADE_LOJA">
              <div class="col-span-3 min-w-0 md:col-span-1">
                <div class="flex flex-wrap items-center gap-1">
                  <span class="truncate font-medium" :title="loja.integracao">{{ loja.loja }}</span>
                  <span v-if="loja.duoke_hoje" class="rounded bg-orange-500/15 px-1 py-px text-[10px] text-orange-800 dark:text-orange-300" title="o Duoke manda esta automação nesta loja hoje (levantamento de 05/10)">Duoke hoje</span>
                  <span v-if="loja.sem_acesso" class="rounded bg-red-500/15 px-1 py-px text-[10px] text-red-700 dark:text-red-300" :title="POR_QUE_NAO_ENVIAR.loja_sem_acesso">sem acesso</span>
                  <span v-if="loja.regra.padrao" class="rounded bg-muted px-1 py-px text-[10px] text-muted-foreground" title="sem regra salva: valem os padrões do catálogo">padrão</span>
                  <span v-if="enviarSemChave(loja, dados.chaves)" class="rounded bg-red-600 px-1 py-px text-[10px] font-semibold text-white" title="regra em ENVIAR com a chave de envio desligada: nada sai — se o Duoke já foi desligado aqui, o comprador fica sem" data-enviar-sem-chave>ENVIAR sem envio</span>
                  <span v-if="loja.regra.disjuntor_em" class="rounded bg-amber-500/15 px-1 py-px text-[10px] text-amber-800 dark:text-amber-300" :title="tituloDisjuntor(loja.regra.disjuntor_em, loja.regra.disjuntor_motivo, fmtDataHora(loja.regra.disjuntor_em))">disjuntor</span>
                  <span
                    v-if="loja.regra.modo === 'simular'"
                    class="rounded px-1 py-px text-[10px]"
                    :class="loja.pode_trocar ? CLS_TROCA_PRONTA : 'bg-muted text-muted-foreground'"
                    :title="tituloTroca(loja, fmtDataHora)"
                    data-troca-loja
                  >{{ seloTroca(loja) }}</span>
                </div>
              </div>
              <div class="col-span-3 flex items-center gap-1 md:col-span-1">
                <select
                  :value="loja.regra.modo"
                  :disabled="!canEdit || salvandoModo === chaveLoja(aut, loja)"
                  class="h-7 min-w-0 flex-1 rounded-md border px-1.5 text-xs disabled:opacity-60"
                  :class="modoAutomacaoInfo(loja.regra.modo).cls"
                  :aria-label="`modo de ${aut.nome} em ${loja.loja}`"
                  :title="modoAutomacaoInfo(loja.regra.modo).hint"
                  data-modo
                  @change="aoEscolherModo(aut, loja, $event)"
                >
                  <option
                    v-for="o in opcoesDeModo(loja, aut)"
                    :key="o.value"
                    :value="o.value"
                    :selected="o.value === loja.regra.modo"
                    :disabled="o.disabled"
                    :title="o.title"
                  >{{ o.label }}</option>
                </select>
                <Loader2 v-if="salvandoModo === chaveLoja(aut, loja)" class="size-3.5 shrink-0 animate-spin text-muted-foreground" />
                <Lock
                  v-else-if="!loja.pode_enviar && loja.regra.modo !== 'enviar'"
                  class="size-3.5 shrink-0 text-muted-foreground"
                  :title="`Enviar travado: ${motivosEnviar(loja.por_que_nao_enviar).join('; ')}`"
                  :aria-label="`Enviar travado: ${motivosEnviar(loja.por_que_nao_enviar).join('; ')}`"
                />
                <button
                  type="button"
                  class="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border px-1.5 hover:bg-muted"
                  :class="editando === chaveLoja(aut, loja) ? 'bg-muted' : ''"
                  :aria-expanded="editando === chaveLoja(aut, loja)"
                  :aria-label="`${canEdit ? 'editar' : 'ver'} a regra de ${aut.nome} em ${loja.loja}`"
                  :title="canEdit ? 'texto, atraso, horário e condições' : 'ver a regra'"
                  @click="abrirEditor(aut, loja)"
                >
                  <Pencil class="size-3.5" />
                  <span class="md:hidden">{{ canEdit ? 'editar' : 'ver' }}</span>
                </button>
              </div>
              <div class="tabular-nums md:text-right">
                <span class="mr-1 text-[10px] text-muted-foreground md:hidden">24 h</span>{{ mandaria(loja.h24) }}
              </div>
              <div class="tabular-nums md:text-right">
                <span class="mr-1 text-[10px] text-muted-foreground md:hidden">{{ periodo }}</span>{{ mandaria(loja.periodo) }}
              </div>
              <div class="cursor-help md:text-right" :title="tituloConta(loja.periodo, aut.tipo, periodo, textosMotivos)">
                <span class="mr-1 text-[10px] text-muted-foreground md:hidden">bateu</span>
                <span class="tabular-nums" :class="clsPct(bateuDe(loja.periodo, aut.tipo).valor)" data-bateu>{{ fmtPct(bateuDe(loja.periodo, aut.tipo).valor) }}</span>
                <span v-if="loja.periodo?.alertas" class="ml-1 rounded bg-red-500/15 px-1 text-[10px] font-medium text-red-700 dark:text-red-300" title="mandaria para quem devolveu, cancelou ou reclamou">alerta</span>
              </div>
              <div class="md:text-right">
                <span class="mr-1 text-[10px] text-muted-foreground md:hidden">só DaVinci</span>
                <button
                  v-if="loja.periodo?.so_davinci"
                  type="button"
                  class="tabular-nums text-amber-800 underline decoration-dotted underline-offset-2 dark:text-amber-300"
                  :title="`ver no registro (${loja.periodo.so_davinci_2d} nos últimos 2 dias)`"
                  @click="verNoRegistro(aut, loja, 'so_davinci')"
                >{{ loja.periodo.so_davinci }}</button>
                <span v-else class="tabular-nums text-muted-foreground">0</span>
              </div>
              <div class="md:text-right">
                <span class="mr-1 text-[10px] text-muted-foreground md:hidden">só Duoke</span>
                <button
                  v-if="loja.periodo?.so_duoke"
                  type="button"
                  class="tabular-nums text-amber-800 underline decoration-dotted underline-offset-2 dark:text-amber-300"
                  title="ver no registro"
                  @click="verNoRegistro(aut, loja, 'so_duoke')"
                >{{ loja.periodo.so_duoke }}</button>
                <span v-else class="tabular-nums text-muted-foreground">0</span>
              </div>
              <div class="col-span-3 min-w-0 text-muted-foreground md:col-span-1">
                <template v-if="loja.ultimo">
                  <span :title="fmtDataHora(loja.ultimo.devido_em)">{{ haQuanto(loja.ultimo.devido_em, agora) }}</span>
                  · <span :title="loja.ultimo.motivo ? (textosMotivos[loja.ultimo.motivo] || loja.ultimo.motivo) : (ESTADOS_REGISTRO[loja.ultimo.estado]?.hint || '')">{{ ESTADOS_REGISTRO[loja.ultimo.estado]?.label || loja.ultimo.estado }}</span>
                </template>
                <span v-else>{{ loja.regra.modo === 'desligado' ? '—' : 'nada ainda' }}</span>
              </div>
            </div>

            <!-- pôr em ENVIAR: só com a confirmação de que o Duoke foi desligado -->
            <div
              v-if="pedindoEnviar === chaveLoja(aut, loja)"
              class="mx-3 mb-2 space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-900 dark:text-amber-200"
              role="group"
              :aria-label="`pôr ${aut.nome} em Enviar em ${loja.loja}`"
              data-pedir-enviar
            >
              <p class="font-medium">A partir daqui o DaVinci MANDA esta mensagem de verdade em {{ loja.loja }}.</p>
              <p>Na mesma hora, desligue "{{ aut.nome }}" desta loja no Duoke — senão o comprador recebe duas. Se o Duoke mandar depois, o DaVinci volta a regra sozinho para Simular.</p>
              <p v-if="aut.tipo === 'opcao' && loja.periodo?.so_davinci" data-sobra-opcao>
                Nas opções o DaVinci responde na hora: em {{ periodo }} ele teria respondido {{ loja.periodo.so_davinci }} {{ loja.periodo.so_davinci === 1 ? 'vez' : 'vezes' }} em que o Duoke não respondeu (o Duoke responde 12 h depois do menu, e só se ninguém respondeu). É de propósito — confirme que é isso que você quer.
              </p>
              <label class="flex items-center gap-2">
                <input v-model="desligueiNoDuoke" type="checkbox" />
                Desliguei esta automação desta loja no Duoke
              </label>
              <div v-if="pedeCriterio(aut, loja)" class="space-y-1 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-red-800 dark:text-red-300" data-criterio-nao-passou>
                <p class="font-medium">O critério da troca não passou nesta loja (últimos 7 dias):</p>
                <ul><li v-for="m in loja.por_que_nao_trocar" :key="m">· {{ m }}</li></ul>
                <label class="flex items-center gap-2">
                  <input v-model="trocaSemCriterio" type="checkbox" />
                  Sei que o critério não passou nesta loja e quero trocar mesmo assim
                </label>
              </div>
              <div class="flex flex-wrap gap-2">
                <Button size="sm" class="h-7 text-xs" :disabled="!desligueiNoDuoke || (pedeCriterio(aut, loja) && !trocaSemCriterio) || salvandoModo === chaveLoja(aut, loja)" @click="confirmarEnviar(aut, loja)">
                  <Loader2 v-if="salvandoModo === chaveLoja(aut, loja)" class="mr-1 size-3.5 animate-spin" /><Check v-else class="mr-1 size-3.5" />
                  pôr em Enviar
                </Button>
                <Button size="sm" variant="ghost" class="h-7 text-xs" @click="pedindoEnviar = null">cancelar</Button>
              </div>
            </div>

            <!-- o editor da regra desta loja -->
            <div
              v-if="editando === chaveLoja(aut, loja)"
              class="space-y-3 border-t bg-muted/20 px-3 py-3"
              :aria-label="`regra de ${aut.nome} em ${loja.loja}`"
              role="group"
              data-editor
            >
              <div class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span class="font-medium">Regra de {{ loja.loja }}</span>
                <span class="text-muted-foreground">
                  <template v-if="loja.regra.padrao">sem regra salva (os padrões do catálogo)</template>
                  <template v-else>versão {{ loja.regra.versao }}<template v-if="loja.regra.ligada_desde"> · ligada {{ haQuanto(loja.regra.ligada_desde, agora) }}</template><template v-if="loja.regra.enviar_desde"> · enviando desde {{ fmtDataHora(loja.regra.enviar_desde) }}</template><template v-if="loja.regra.atualizado_em"> · mudou {{ haQuanto(loja.regra.atualizado_em, agora) }}</template></template>
                </span>
                <button type="button" class="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground" aria-label="fechar a regra" @click="fecharEditor"><X class="size-3.5" /> fechar</button>
              </div>

              <div class="grid gap-3 lg:grid-cols-2">
                <!-- o texto -->
                <div class="space-y-2">
                  <template v-for="(p, i) in form.partes" :key="i">
                    <div v-if="temTexto(p)" class="space-y-1">
                      <label :for="`texto-${chaveLoja(aut, loja)}-${i}`" class="text-[11px] font-medium">
                        {{ rotuloParte(form.partes, i) }}
                      </label>
                      <textarea
                        :id="`texto-${chaveLoja(aut, loja)}-${i}`"
                        :ref="(el) => guardarCaixa(i, el)"
                        v-model="p.texto"
                        rows="6"
                        maxlength="2000"
                        class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm leading-snug disabled:opacity-70"
                        :disabled="!canEdit || aut.travada"
                        :placeholder="aut.travada ? 'sem texto até o painel do Duoke dizer qual é' : 'o texto que o comprador recebe'"
                        data-texto
                        @focus="focoCaixa = i"
                      />
                      <div class="flex justify-end text-[10px] tabular-nums text-muted-foreground">
                        <span
                          :class="limiteDaParte(aut, p) && tamanhoComExemplo(p.texto, aut.plataforma) > (limiteDaParte(aut, p) || 0) ? 'font-semibold text-red-600 dark:text-red-400' : ''"
                          title="contado como sai, com o nome de exemplo no lugar do {comprador}"
                        >{{ tamanhoComExemplo(p.texto, aut.plataforma) }}<template v-if="limiteDaParte(aut, p)"> / {{ limiteDaParte(aut, p) }}</template></span>
                      </div>
                    </div>
                    <div v-else class="inline-flex items-center gap-1.5 rounded border border-dashed px-2 py-1 text-[11px] text-muted-foreground" :title="p.tipo === 'cartao_pedido' ? 'o cartão do pedido vai junto, como no Duoke' : `figurinha ${p.figurinha || ''} (${p.pacote || ''})`">
                      {{ p.tipo === 'cartao_pedido' ? 'Cartão do pedido (vai junto)' : 'Figurinha (vai junto)' }}
                    </div>
                  </template>

                  <div class="rounded-md border bg-background px-2 py-1.5 text-[11px]" data-placeholders>
                    <div class="mb-0.5 font-medium">Lacunas que o DaVinci troca na hora de mandar</div>
                    <ul class="space-y-0.5">
                      <li v-for="(desc, nome) in aut.placeholders" :key="nome" class="flex flex-wrap items-baseline gap-1">
                        <button
                          type="button"
                          class="rounded bg-muted px-1 font-mono hover:bg-muted/70 disabled:opacity-60"
                          :disabled="!canEdit || aut.travada"
                          :title="`inserir ${lacuna(nome)} no texto`"
                          @click="inserirPlaceholder(String(nome))"
                        >{{ lacuna(nome) }}</button>
                        <span class="text-muted-foreground">{{ desc }}</span>
                      </li>
                    </ul>
                    <p class="mt-1 text-muted-foreground">Sem o nome, a frase se ajeita sozinha: "Oi, {comprador}! …" sai "Oi! …". Outra lacuna entre chaves não existe e o validador barra.</p>
                  </div>
                </div>

                <!-- a prévia (o backend: nome de exemplo + validador) -->
                <div class="space-y-1" aria-live="polite" data-previa>
                  <div class="flex items-center gap-1.5 text-[11px] font-medium">
                    Como o comprador vê <span class="font-normal text-muted-foreground">(com o nome de exemplo {{ previa?.comprador_exemplo || NOME_EXEMPLO }})</span>
                    <Loader2 v-if="previaCarregando" class="size-3 animate-spin text-muted-foreground" />
                  </div>
                  <div class="space-y-1.5 rounded-md border bg-background p-2">
                    <template v-if="previa">
                      <div v-for="(p, i) in previa.partes" :key="`p${i}`" class="flex justify-end">
                        <div v-if="p.tipo === 'texto'" class="max-w-[95%] whitespace-pre-wrap break-words rounded-lg rounded-tr-sm bg-sky-100 px-3 py-2 text-xs dark:bg-sky-900/45">{{ p.texto }}</div>
                        <div v-else-if="p.tipo === 'resposta_publica'" class="w-full whitespace-pre-wrap break-words rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs">
                          <div class="mb-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-300">Resposta pública na avaliação</div>{{ p.texto }}
                        </div>
                        <div v-else class="rounded border border-dashed px-2 py-1 text-[11px] text-muted-foreground">{{ p.tipo === 'cartao_pedido' ? 'cartão do pedido' : 'figurinha' }}</div>
                      </div>
                      <p v-if="!previa.partes.length" class="text-muted-foreground">sem texto</p>
                      <details v-if="semNomeDiferente" class="text-[11px] text-muted-foreground">
                        <summary class="cursor-pointer">sem o nome do comprador</summary>
                        <div v-for="(p, i) in previa.sem_nome.filter((x) => temTexto(x))" :key="`s${i}`" class="mt-1 whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1">{{ p.texto }}</div>
                      </details>
                    </template>
                    <p v-else-if="previaErro" class="text-red-700 dark:text-red-300">{{ previaErro }}</p>
                    <p v-else class="text-muted-foreground">montando a prévia…</p>
                  </div>
                  <ul v-if="previa?.motivos?.length" class="space-y-0.5 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-700 dark:text-red-300" data-motivos-validador>
                    <li class="font-medium">O validador barra este texto:</li>
                    <li v-for="m in previa.motivos" :key="m">· {{ m }}</li>
                  </ul>
                  <p v-else-if="previa" class="flex items-center gap-1 text-[11px] text-emerald-700 dark:text-emerald-300"><Check class="size-3" /> passa no validador</p>
                </div>
              </div>

              <!-- quando -->
              <div class="grid gap-3 sm:grid-cols-3">
                <label class="space-y-1">
                  <span class="block text-[11px] font-medium">Atraso depois do gatilho (min)</span>
                  <input v-model.number="form.atraso_min" type="number" min="0" max="10080" step="1" inputmode="numeric" class="h-8 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-70" :disabled="!canEdit" />
                  <span class="block text-[10px] text-muted-foreground">= {{ fmtAtraso(Number(form.atraso_min) || 0) }} · padrão {{ fmtAtraso(aut.atraso_min) }}</span>
                </label>
                <div class="space-y-1">
                  <span class="block text-[11px] font-medium">Horário (Brasília)</span>
                  <label class="flex items-center gap-1.5 text-[11px]">
                    <input v-model="form.dia_todo" type="checkbox" :disabled="!canEdit" /> o dia todo
                  </label>
                  <div v-if="!form.dia_todo" class="flex items-center gap-1">
                    <input v-model="form.janela_inicio" type="time" class="h-8 min-w-0 flex-1 rounded-md border bg-background px-1.5 text-sm" aria-label="início do horário" :disabled="!canEdit" />
                    <span class="text-muted-foreground">às</span>
                    <input v-model="form.janela_fim" type="time" class="h-8 min-w-0 flex-1 rounded-md border bg-background px-1.5 text-sm" aria-label="fim do horário" :disabled="!canEdit" />
                  </div>
                  <span class="block text-[10px] text-muted-foreground">fora do horário, espera a próxima abertura · padrão {{ janelaLegivel(aut.janela_inicio, aut.janela_fim) }}</span>
                </div>
                <label class="space-y-1">
                  <span class="block text-[11px] font-medium">Teto por dia nesta loja</span>
                  <input v-model="form.teto_dia" type="number" min="0" max="6000" step="1" inputmode="numeric" class="h-8 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-70" :placeholder="dados.chaves.teto_dia ? `vazio = o geral (${dados.chaves.teto_dia})` : 'vazio = sem teto'" :disabled="!canEdit" />
                  <span class="block text-[10px] text-muted-foreground">no modo seco, passar do teto só vira "não mandaria"</span>
                </label>
              </div>

              <!-- condições do catálogo -->
              <div v-if="condicoesDoEditor.length" class="space-y-1">
                <span class="block text-[11px] font-medium">Condições</span>
                <div class="grid gap-1.5 sm:grid-cols-2">
                  <template v-for="c in condicoesDoEditor" :key="c.chave">
                    <label v-if="typeof c.padrao === 'boolean'" class="flex items-start gap-1.5" :title="c.hint || ''">
                      <input v-model="form.condicoes[c.chave]" type="checkbox" class="mt-0.5" :disabled="!canEdit" />
                      <span>{{ c.label }}<span v-if="form.condicoes[c.chave] !== c.padrao" class="ml-1 text-[10px] text-amber-700 dark:text-amber-300">(o padrão é {{ c.padrao ? 'ligado' : 'desligado' }})</span></span>
                    </label>
                    <label v-else class="flex flex-wrap items-center gap-1.5" :title="c.hint || ''">
                      <span>{{ c.label }}</span>
                      <input v-model.number="form.condicoes[c.chave]" type="number" min="1" step="1" class="h-7 w-16 rounded-md border bg-background px-1.5 text-sm" :disabled="!canEdit" :aria-label="c.label" />
                      <span class="text-muted-foreground">{{ c.unidade }}<template v-if="form.condicoes[c.chave] !== c.padrao"> (padrão {{ c.padrao }})</template></span>
                    </label>
                  </template>
                </div>
              </div>

              <!-- o que segura o salvar -->
              <ul v-if="problemas.length" class="space-y-0.5 text-[11px] text-red-700 dark:text-red-300" data-problemas>
                <li v-for="pr in problemas" :key="pr">· {{ pr }}</li>
              </ul>
              <div v-if="erroSalvar" class="rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-700 dark:text-red-300" role="alert">
                <div class="font-medium">{{ erroSalvar.texto }}</div>
                <ul v-if="erroSalvar.motivos.length"><li v-for="m in erroSalvar.motivos" :key="m">· {{ m }}</li></ul>
              </div>

              <div v-if="canEdit" class="flex flex-wrap items-center gap-2">
                <Button size="sm" class="h-8 text-xs" :disabled="!mudou || !!problemas.length || salvandoRegra || aut.travada" data-salvar @click="salvarRegra">
                  <Loader2 v-if="salvandoRegra" class="mr-1 size-3.5 animate-spin" /><Save v-else class="mr-1 size-3.5" />
                  salvar
                </Button>
                <Button size="sm" variant="ghost" class="h-8 text-xs" :disabled="!mudou || salvandoRegra" @click="desfazer">
                  <Undo2 class="mr-1 size-3.5" /> desfazer
                </Button>
                <span class="text-[11px] text-muted-foreground">
                  {{ mudou ? 'mudou: salve para valer' : 'nada mudou' }} · texto e condições sobem a versão; o modo muda no seletor da linha.
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- o registro recente: SEM texto nenhum -->
    <section ref="registroEl" class="overflow-hidden rounded-lg border bg-card" aria-labelledby="registro-automaticas" data-registro>
      <header class="space-y-2 border-b px-3 py-2">
        <div class="flex flex-wrap items-baseline gap-x-2">
          <h3 id="registro-automaticas" class="text-sm font-medium">Registro recente</h3>
          <span class="text-xs text-muted-foreground">o que o DaVinci mandaria (ou mandou), para qual pedido ou conversa, e o que o Duoke fez — sem o texto de ninguém; o <Eye class="inline size-3" /> de cada linha mostra como o cliente receberia, montado na hora, ao lado do que o Duoke mandou</span>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <select v-model="filtrosRegistro.automacao" class="h-8 w-full rounded-md border bg-background px-2 sm:w-auto sm:max-w-[16rem]" aria-label="automação">
            <option value="">toda automação</option>
            <option v-for="a in automacoes" :key="a.codigo" :value="a.codigo">{{ a.nome }}</option>
          </select>
          <select v-model="filtrosRegistro.integration_id" class="h-8 w-full rounded-md border bg-background px-2 sm:w-auto" aria-label="loja">
            <option value="">toda loja</option>
            <option v-for="lj in lojasDoRegistro" :key="lj.id" :value="lj.id">{{ lj.nome }}</option>
          </select>
          <select v-model="filtrosRegistro.estado" class="h-8 w-full rounded-md border bg-background px-2 sm:w-auto" aria-label="estado">
            <option value="">todo estado</option>
            <option v-for="(e, codigo) in ESTADOS_REGISTRO" :key="codigo" :value="codigo">{{ e.label }}</option>
          </select>
          <select v-model="filtrosRegistro.comparacao" class="h-8 w-full rounded-md border bg-background px-2 sm:w-auto" aria-label="comparação com o Duoke">
            <option v-for="f in FILTROS_COMPARACAO" :key="f.value" :value="f.value">{{ f.label }}</option>
          </select>
          <button v-if="filtrandoRegistro" type="button" class="text-muted-foreground underline hover:text-foreground" @click="limparFiltrosRegistro">limpar filtros</button>
          <Loader2 v-if="registroCarregando" class="size-3.5 animate-spin text-muted-foreground" />
        </div>
      </header>

      <div v-if="registroErro" class="border-b border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-700 dark:text-red-300" role="alert">
        {{ registroErro }} <button type="button" class="ml-2 underline" @click="carregarRegistro">tentar de novo</button>
      </div>
      <div v-if="registro.length" class="hidden gap-x-3 bg-muted/40 px-3 py-1.5 text-[11px] font-semibold text-muted-foreground md:grid" :class="GRADE_REGISTRO">
        <span title="quando mandaria (ou mandou)">Quando</span>
        <span>Automação · loja</span>
        <span>Para</span>
        <span>Estado</span>
        <span>Duoke</span>
        <span>Comparação</span>
        <span class="sr-only">abrir</span>
      </div>
      <p v-if="registroCarregando && !registro.length" class="px-3 py-6 text-center text-xs text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando o registro…</p>
      <p v-else-if="!registro.length && !registroErro" class="px-3 py-6 text-center text-xs text-muted-foreground" data-registro-vazio>
        {{ filtrandoRegistro ? 'Nada no registro com esses filtros.' : dados && !dados.chaves.motor_ativo ? 'Nada no registro ainda: o motor está desligado no servidor (ATENDIMENTO_AUTOMACOES_ATIVA). Ligado, cada mensagem que o DaVinci mandaria aparece aqui.' : 'Nada no registro ainda: cada mensagem que o DaVinci mandaria aparece aqui na rodada seguinte (a cada 2 min).' }}
      </p>
      <ul class="divide-y text-xs">
        <li v-for="l in registro" :key="l.id" class="grid grid-cols-2 items-start gap-x-3 gap-y-1 px-3 py-2 md:items-center" :class="GRADE_REGISTRO" :data-registro-linha="l.id">
          <div class="tabular-nums" :title="l.evento_em ? `aconteceu ${fmtDataHora(l.evento_em)}${l.decidido_em ? ` · decidido ${fmtDataHora(l.decidido_em)}` : ''}` : ''">
            {{ fmtDataHora(l.devido_em) }}
          </div>
          <div class="order-first col-span-2 min-w-0 md:order-none md:col-span-1">
            <div class="flex min-w-0 items-center gap-1">
              <AtendimentoPlataforma :codigo="l.plataforma" />
              <span class="truncate font-medium" :title="l.automacao_nome">{{ l.automacao_nome }}</span>
            </div>
            <div class="truncate text-muted-foreground">{{ l.loja || '—' }}<template v-if="l.regra_versao"> · v{{ l.regra_versao }}</template></div>
          </div>
          <div class="min-w-0 truncate text-muted-foreground" :title="alvoDaLinha(l)">{{ alvoDaLinha(l) }}</div>
          <div class="min-w-0">
            <span class="rounded px-1.5 py-px text-[11px]" :class="estadoDaLinha(l).cls" :title="estadoDaLinha(l).hint">{{ estadoDaLinha(l).label }}</span>
            <div v-if="l.motivo_texto" class="mt-0.5 text-[11px] text-muted-foreground">{{ l.motivo_texto }}</div>
            <div v-else-if="l.motivo" class="mt-0.5 text-[11px] text-muted-foreground">{{ l.motivo }}</div>
            <div v-if="l.erro" class="mt-0.5 text-[11px] text-red-700 dark:text-red-300" :title="l.erro">a plataforma recusou<template v-if="l.tentativas > 1"> ({{ l.tentativas }} tentativas)</template></div>
          </div>
          <div class="min-w-0 text-muted-foreground" :title="DUOKE_REGISTRO[l.duoke]?.hint || ''">
            {{ DUOKE_REGISTRO[l.duoke]?.label || l.duoke }}
            <div v-if="l.duoke === 'mandou' && l.duoke_diferenca_s !== null" class="text-[11px]" :title="l.duoke_em ? fmtDataHora(l.duoke_em) : ''">{{ fmtDiferenca(l.duoke_diferenca_s, false) }}</div>
          </div>
          <div class="min-w-0">
            <span v-if="comparacaoInfo(l)" class="rounded px-1.5 py-px text-[11px]" :class="comparacaoInfo(l)?.cls" :title="comparacaoInfo(l)?.hint" data-comparacao>{{ comparacaoInfo(l)?.label }}</span>
            <div v-if="l.alerta" class="mt-0.5 inline-flex items-center gap-1 rounded bg-red-500/15 px-1.5 py-px text-[11px] font-medium text-red-700 dark:text-red-300" :title="l.alerta_texto || l.alerta" data-alerta><ShieldAlert class="size-3" /> {{ l.alerta_texto || l.alerta }}</div>
            <div v-if="l.divergencia_texto" class="mt-0.5 text-[11px] text-muted-foreground">{{ l.divergencia_texto }}</div>
          </div>
          <div class="flex items-center justify-end gap-2 md:flex-col md:items-end md:gap-1">
            <button
              type="button"
              class="inline-flex items-center gap-1 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              :class="previaAberta === l.id ? 'text-foreground' : ''"
              :aria-expanded="previaAberta === l.id"
              :aria-label="`como o cliente receberia ${l.automacao_nome} em ${l.loja || 'loja'}, ao lado do Duoke`"
              title="como o cliente receberia (montado agora) × o que o Duoke mandou"
              data-abrir-previa
              @click="abrirPreviaDaLinha(l)"
            ><Eye class="size-3.5" /><span class="md:hidden">como o cliente recebe</span></button>
            <button
              v-if="l.conversa_id"
              type="button"
              class="inline-flex items-center gap-1 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              :aria-label="`abrir a conversa de ${l.automacao_nome} em ${l.loja || 'loja'} na Caixa`"
              title="abrir a conversa na Caixa"
              @click="abrirConversa(l.conversa_id)"
            ><ExternalLink class="size-3.5" /><span class="md:hidden">abrir conversa</span></button>
          </div>

          <!-- a prévia da linha: DaVinci (montado agora) × Duoke (de verdade) -->
          <div
            v-if="previaAberta === l.id"
            class="col-span-2 mt-1 rounded-md border bg-muted/20 p-2 md:col-span-7"
            role="region"
            :aria-label="`como o cliente receberia ${l.automacao_nome}`"
            data-previa-linha
          >
            <p v-if="previaLinhaCarregando" class="flex items-center gap-1.5 text-muted-foreground"><Loader2 class="size-3.5 animate-spin" /> montando a prévia…</p>
            <p v-else-if="previaLinhaErro" class="text-red-700 dark:text-red-300" role="alert">{{ previaLinhaErro }}</p>
            <template v-else-if="previaLinha">
              <div class="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                <span class="font-medium">Como o cliente receberia</span>
                <span class="text-muted-foreground">— montado agora, nada fica gravado</span>
                <span v-if="previaLinha.automacao.so_simulacao" class="rounded bg-violet-500/15 px-1.5 py-px text-violet-700 dark:text-violet-300" :title="previaLinha.automacao.so_simulacao_texto || ''">só simulação</span>
                <span v-if="previaLinha.duoke.comparacao" class="rounded px-1.5 py-px" :class="COMPARACOES[previaLinha.duoke.comparacao]?.cls" :title="COMPARACOES[previaLinha.duoke.comparacao]?.hint" data-previa-comparacao>{{ COMPARACOES[previaLinha.duoke.comparacao]?.label || previaLinha.duoke.comparacao }}</span>
                <span v-if="versaoMudou(previaLinha.davinci)" class="rounded bg-amber-500/15 px-1.5 py-px text-amber-800 dark:text-amber-300" data-previa-versao>o texto mudou: esta é a regra de agora (v{{ previaLinha.davinci.versao_regra }}); a linha foi decidida na v{{ previaLinha.davinci.versao_da_linha }}</span>
                <button type="button" class="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground" aria-label="fechar a prévia" @click="fecharPreviaDaLinha"><X class="size-3.5" /> fechar</button>
              </div>
              <div class="grid gap-3 md:grid-cols-2">
                <!-- DaVinci -->
                <section class="min-w-0 space-y-1" data-lado="davinci">
                  <div class="text-[11px] font-medium">
                    DaVinci
                    <span class="font-normal text-muted-foreground">· {{ previaLinha.davinci.de_verdade ? 'o que saiu' : 'o que sairia' }}<template v-if="previaLinha.davinci.hora"> · {{ HORA_DAVINCI[previaLinha.davinci.hora_tipo] || '' }} {{ fmtDataHora(previaLinha.davinci.hora) }}</template></span>
                  </div>
                  <p v-if="porQueNaoSairia(previaLinha.davinci)" class="rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground" data-nao-sairia>{{ porQueNaoSairia(previaLinha.davinci) }}</p>
                  <div class="space-y-1.5 rounded-md bg-background p-2" :class="previaLinha.davinci.sairia ? '' : 'opacity-60'">
                    <div v-for="(pc, i) in previaLinha.davinci.partes" :key="`d${i}`" class="flex flex-col items-end" :data-parte-davinci="pc.tipo">
                      <div v-if="pc.tipo === 'resposta_publica'" class="w-full whitespace-pre-wrap break-words rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
                        <div class="mb-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-300">Resposta pública na avaliação</div>{{ pc.texto }}
                      </div>
                      <div v-else-if="pc.tipo === 'cartao_pedido'" class="rounded-lg rounded-tr-sm bg-sky-100 p-2 dark:bg-sky-900/45">
                        <div class="w-[240px] max-w-full rounded-md bg-background p-3 text-[13px] leading-5 shadow-sm">ID do Pedido<span class="font-semibold">#{{ pc.pedido || '—' }}</span><div class="text-[11px] text-muted-foreground">cartão do pedido</div></div>
                      </div>
                      <div v-else-if="pc.tipo === 'figurinha'" class="rounded-lg rounded-tr-sm bg-sky-100 p-2 dark:bg-sky-900/45">
                        <img v-if="pc.imagem_url" :src="pc.imagem_url" :alt="`figurinha ${pc.figurinha || ''}`" loading="lazy" referrerpolicy="no-referrer" class="size-20 object-contain" />
                        <span v-else class="text-xs text-muted-foreground">[figurinha {{ pc.figurinha || '' }}]</span>
                      </div>
                      <div v-else-if="pc.texto" class="max-w-[90%] whitespace-pre-wrap break-words rounded-lg rounded-tr-sm bg-sky-100 px-3 py-2 text-sm dark:bg-sky-900/45">{{ pc.texto }}</div>
                      <div v-else class="rounded border border-dashed px-2 py-1 text-[11px] text-muted-foreground">{{ PARTES_VISTA[pc.tipo] || pc.tipo }}</div>
                      <span v-if="pc.nota" class="mt-0.5 text-[10px] text-muted-foreground">{{ pc.nota }}</span>
                    </div>
                    <p v-if="!previaLinha.davinci.partes.length" class="text-muted-foreground">nada para mostrar</p>
                  </div>
                  <p v-if="previaLinha.davinci.comprador" class="text-[11px] text-muted-foreground">com o usuário do comprador: <span class="font-medium text-foreground">{{ previaLinha.davinci.comprador }}</span></p>
                  <p v-if="previaLinha.davinci.valores.valor_cupom" class="text-[11px] text-muted-foreground">cupom de R$ {{ previaLinha.davinci.valores.valor_cupom }} (pela faixa do valor do pedido)</p>
                  <ul v-if="previaLinha.davinci.motivos_validador.length" class="text-[11px] text-red-700 dark:text-red-300"><li v-for="m in previaLinha.davinci.motivos_validador" :key="m">· {{ m }}</li></ul>
                </section>
                <!-- Duoke -->
                <section class="min-w-0 space-y-1" data-lado="duoke">
                  <div class="text-[11px] font-medium">
                    Duoke <span class="font-normal text-muted-foreground">· o que mandou de verdade</span>
                  </div>
                  <div class="space-y-1.5 rounded-md bg-background p-2">
                    <div v-for="(pc, i) in previaLinha.duoke.partes" :key="`k${i}`" class="flex flex-col items-end" :data-parte-duoke="pc.tipo">
                      <div v-if="pc.tipo === 'resposta_publica'" class="w-full whitespace-pre-wrap break-words rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
                        <div class="mb-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-300">Resposta pública na avaliação</div>{{ pc.texto }}
                      </div>
                      <div v-else-if="pc.tipo === 'cartao_pedido'" class="rounded-lg rounded-tr-sm bg-sky-100 p-2 dark:bg-sky-900/45">
                        <div class="w-[240px] max-w-full rounded-md bg-background p-3 text-[13px] leading-5 shadow-sm">ID do Pedido<span class="font-semibold">#{{ pc.pedido || '—' }}</span><div class="text-[11px] text-muted-foreground">cartão do pedido</div></div>
                      </div>
                      <div v-else-if="pc.tipo === 'figurinha'" class="rounded-lg rounded-tr-sm bg-sky-100 p-2 dark:bg-sky-900/45">
                        <img v-if="pc.imagem_url" :src="pc.imagem_url" :alt="`figurinha ${pc.figurinha || ''}`" loading="lazy" referrerpolicy="no-referrer" class="size-20 object-contain" />
                        <span v-else class="text-xs text-muted-foreground">[figurinha {{ pc.figurinha || '' }}]</span>
                      </div>
                      <div v-else-if="pc.texto" class="max-w-[90%] whitespace-pre-wrap break-words rounded-lg rounded-tr-sm bg-sky-100 px-3 py-2 text-sm dark:bg-sky-900/45" :class="pc.principal ? '' : 'opacity-90'">{{ pc.texto }}</div>
                      <div v-else class="rounded border border-dashed px-2 py-1 text-[11px] text-muted-foreground">{{ PARTES_VISTA[pc.tipo] || pc.tipo }}</div>
                      <span class="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
                        <template v-if="pc.em">{{ fmtDataHora(pc.em) }}</template><template v-if="pc.diferenca_s !== null"> · {{ fmtDiferenca(pc.diferenca_s, false) }} da nossa</template><template v-if="pc.nota"> · {{ pc.nota }}</template>
                      </span>
                    </div>
                    <p v-if="!previaLinha.duoke.partes.length" class="text-muted-foreground" data-sem-duoke>{{ semDuoke(previaLinha.duoke, fmtDataHora) }}</p>
                  </div>
                </section>
              </div>
            </template>
          </div>
        </li>
      </ul>
      <div v-if="registroProximo" class="border-t px-3 py-2 text-center">
        <Button size="sm" variant="outline" class="h-8 text-xs" :disabled="registroMais" @click="carregarMaisRegistro">
          <Loader2 v-if="registroMais" class="mr-1 size-3.5 animate-spin" /> carregar mais
        </Button>
      </div>
    </section>
  </div>
</template>
