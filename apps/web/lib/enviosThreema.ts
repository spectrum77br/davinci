// Cadastros › Envios Threema (tarefa 16): tipos (espelham
// app/services/threema_envios_tela.py) e textos da tela. Puro: testado em
// tests/envios-threema-sfc.cjs.

export type Faixa = { tipo: 'ambar' | 'vermelho' | 'cinza' | 'azul'; situacao: string; texto: string }
export type NomeQuem = { nome: string; tipo: string }
export type Ultimo = {
  lote_id: string
  criado_em: string | null
  titulo: string | null
  total: number
  entregues: number
  falhas: number
  erro: string | null
  origem: string | null
  onde_roda: string | null
  via: string | null
}
export type Depende = { tipo: string; alvo: string; texto: string; ok: boolean; estado: string }
export type Envio = {
  chave: string
  painel: string
  aba: string | null
  nome: string
  descricao: string
  frequencia_tipo: 'evento' | 'horario' | 'manual' | 'misto'
  frequencia_texto: string
  onde_roda: 'servidor' | 'mac' | 'externo'
  remetente: string | null
  gerenciado: boolean
  migrado: boolean
  fatia: string | null
  interruptor: 'proprio' | 'robo_modo' | 'robo_config' | 'fixo'
  ao_desligar: 'descarta' | 'acumula' | 'recusa' | '—'
  ao_desligar_texto: string
  escopo_tipo: string | null
  editavel: boolean
  desligavel: boolean
  por_que_nao_edita: string | null
  onde_configurar: string | null
  tem_reaviso: boolean
  reaviso_horas: number | null
  ativo: boolean
  ativo_texto: string
  ativo_cadastro: boolean | null
  ativo_alterado_por: string | null
  ativo_alterado_em: string | null
  observacao: string | null
  semeado_em: string | null
  quem_recebe: {
    via: string
    motivo: string | null
    fonte: string | null
    nomes: NomeQuem[]
    avisos: string[]
    total: number
  }
  depende_de: Depende[]
  ultimo: Ultimo | null
  situacao: string
  situacoes: string[]
  faixas: Faixa[]
}
export type Emergencia = {
  usar_legado: boolean
  alterado_por: string | null
  alterado_em: string | null
  env_desligado: boolean
}
export type Lista = {
  gerado_em: string
  emergencia: Emergencia
  paineis: string[]
  contagem: Record<string, number>
  total: number
  envios: Envio[]
  pessoa: { id: string; nome: string; tem_threema: boolean } | null
}
export type RefOut = {
  tipo: 'usuario' | 'grupo' | 'contato'
  id: string
  nome: string
  valido: boolean
  aviso: string | null
  membros?: RefOut[]
}
export type Regra = {
  id: string
  plataforma: string | null
  status_plataforma: string | null
  tem_mensagem: boolean
  mensagem: string
  usa_padrao: boolean
  antes: { nomes: NomeQuem[]; avisos: string[] }
  cadastro: RefOut[]
}
export type Detalhe = Envio & {
  destinatarios: RefOut[]
  antes: { fonte: string; ativo: boolean; nomes: NomeQuem[]; avisos: string[] } | null
  regras: Regra[]
  emergencia: Emergencia
}
export type LoteDestinatario = {
  nome: string | null
  id: string
  ok: boolean
  http_status: number | null
  erro: string | null
}
export type Lote = {
  lote_id: string
  criado_em: string | null
  origem: 'automatico' | 'manual' | 'teste'
  onde_roda: string
  via: string | null
  ator: string | null
  titulo: string | null
  escopo: string | null
  contexto: string | null
  total: number
  entregues: number
  falhas: number
  erro_geral: string | null
  destinatarios: LoteDestinatario[]
}

export const FREQUENCIA: Record<Envio['frequencia_tipo'], string> = {
  evento: 'Evento',
  horario: 'Horário',
  manual: 'Manual',
  misto: 'Misto',
}
export const ONDE_RODA: Record<Envio['onde_roda'], string> = {
  servidor: 'Servidor',
  mac: 'Mac do escritório',
  externo: 'Fora do DaVinci',
}
export const SITUACAO: Record<string, { rotulo: string }> = {
  ok: { rotulo: 'Em dia' },
  legado: { rotulo: 'Configuração antiga' },
  ninguem: { rotulo: 'Ninguém recebe' },
  invalido: { rotulo: 'Alguém fica de fora' },
  desligado: { rotulo: 'Desligado' },
  bloqueado: { rotulo: 'Depende de algo desligado' },
  falhando: { rotulo: 'Último envio falhou' },
  mac_env: { rotulo: 'O Mac usa o .env' },
  informativo: { rotulo: 'Informativo' },
  externo: { rotulo: 'Fora do DaVinci' },
}

export function classeFaixa(tipo: Faixa['tipo']): string {
  if (tipo === 'vermelho') return 'border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300'
  if (tipo === 'ambar') return 'border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300'
  if (tipo === 'azul') return 'border-sky-300 bg-sky-50 text-sky-800 dark:bg-sky-950/30 dark:text-sky-300'
  return 'border-border bg-muted/40 text-muted-foreground'
}

/** "A, B, C e mais 2" (só nomes). */
export function quemRecebeTexto(e: Pick<Envio, 'quem_recebe' | 'gerenciado'>, max = 4): string {
  if (!e.gerenciado) return '—'
  const nomes = e.quem_recebe.nomes.map((n) => n.nome)
  if (!nomes.length) return 'ninguém'
  if (nomes.length <= max) return nomes.join(', ')
  return `${nomes.slice(0, max).join(', ')} e mais ${nomes.length - max}`
}

/** "há 5 min", "há 2 h", "há 3 dias". */
export function haQuanto(iso: string | null | undefined, agora: Date = new Date()): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const min = Math.max(0, Math.round((agora.getTime() - d.getTime()) / 60000))
  if (min < 1) return 'agora'
  if (min < 60) return `há ${min} min`
  const h = Math.round(min / 60)
  if (h < 48) return `há ${h} h`
  return `há ${Math.round(h / 24)} dias`
}

const ERROS: Record<string, string> = {
  http_400: 'pedido recusado (400)',
  http_401: 'remetente recusado (401)',
  http_402: 'sem crédito (402)',
  http_404: 'ID inexistente (404)',
  http_413: 'mensagem grande demais (413)',
  sem_destinatario_valido: 'ninguém válido',
  threema_recipients_missing: 'ninguém para receber',
  threema_gateway_id_missing: 'remetente sem ID',
  threema_gateway_secret_missing: 'remetente sem senha',
  threema_contexto_desativado: 'canal desativado',
}
export function fraseErro(e: string | null | undefined): string {
  if (!e) return ''
  return ERROS[e] || e
}

/** "há 2 h, 3/3" ou "há 2 h, falhou: sem crédito (402)". */
export function ultimoEnvioTexto(u: Ultimo | null, agora: Date = new Date()): string {
  if (!u) return 'nenhum registrado'
  const quando = haQuanto(u.criado_em, agora)
  if (!u.total) return `${quando}, não saiu: ${fraseErro(u.erro) || 'erro'}`
  if (u.falhas) return `${quando}, ${u.entregues}/${u.total} — falhou: ${fraseErro(u.erro) || 'erro'}`
  return `${quando}, ${u.entregues}/${u.total}`
}

export const ORIGEM: Record<Lote['origem'], string> = {
  automatico: 'automático',
  manual: 'botão',
  teste: 'teste',
}
