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
  // A conversa por onde sai DE VERDADE (apelidos + canais do servidor);
  // 'desativado' = o canal está desligado no servidor. null = Mac/informativo.
  canal: string | null
  gerenciado: boolean
  migrado: boolean
  fatia: string | null
  interruptor: 'proprio' | 'robo_modo' | 'robo_config' | 'fixo'
  ao_desligar: 'descarta' | 'acumula' | 'recusa' | '—'
  ao_desligar_texto: string
  escopo_tipo: string | null
  editavel: boolean
  desligavel: boolean
  por_que_nao_desliga: string | null
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
    // Item 1: quantas regras têm lista própria e quem recebe só por elas.
    por_regra?: { regras: number; extras: string[] } | null
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
  mac_env: { rotulo: 'O Mac ainda usa a lista dele' },
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

/** Robô da Ouvidoria desligado de propósito: "ninguém recebe" não é alarme
 *  (o aviso não sai de qualquer jeito; a faixa cinza já lembra de escolher
 *  quem antes de religar). */
export function roboDesligado(e: Pick<Envio, 'depende_de'>): boolean {
  return e.depende_de.some((d) => d.tipo === 'robo_modo' && d.estado === 'desligado')
}

/** Item 1: "lista própria em 9 regras (+ harry potter)" — quem recebe pelo
 *  👤 de uma regra não pode sumir da coluna. '' quando não há. */
export function porRegraTexto(e: Pick<Envio, 'quem_recebe'>): string {
  const p = e.quem_recebe.por_regra
  if (!p || !p.regras) return ''
  const regras = `lista própria em ${p.regras} ${p.regras === 1 ? 'regra' : 'regras'}`
  return p.extras.length ? `${regras} (+ ${p.extras.join(', ')})` : regras
}

// O nome de gente de cada remetente/conversa do Threema.
const CONVERSAS: Record<string, string> = {
  geral: 'Geral',
  logistica: 'Logística',
  logistica_amazon: 'Logística (Amazon)',
  chamados: 'Chamados',
  estoque: 'Estoque',
  controle_estoque: 'Controle de Estoque',
  margem: 'Margem',
  margem_auto: 'Margem (automático)',
  devolucoes: 'Devoluções',
  juridico: 'Jurídico',
  importacao: 'Importação',
  flex: 'Flex',
}
export function nomeConversa(c: string | null | undefined): string {
  if (!c) return ''
  return CONVERSAS[c] || c
}

/** "Logística · sai pela conversa Geral" (o remetente do código pode ser um
 *  apelido; o servidor decide a conversa de verdade). */
export function remetenteTexto(e: Pick<Envio, 'remetente' | 'canal' | 'onde_roda'>): string {
  if (e.onde_roda === 'mac') return 'o do Mac'
  if (!e.remetente) return e.onde_roda === 'servidor' ? 'depende da origem do chamado' : '—'
  const nome = nomeConversa(e.remetente)
  if (e.canal === 'desativado') return `${nome} · canal desativado no servidor (não sai)`
  if (!e.canal || e.canal === e.remetente) return nome
  return `${nome} · sai pela conversa ${nomeConversa(e.canal)}`
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

// O que o Threema respondeu (ou por que não saiu), em frase de gente. Um só
// dicionário para o Cadastro e o Histórico falarem igual.
const ERROS: Record<string, string> = {
  http_400: 'pedido recusado pelo Threema (400)',
  http_401: 'remetente recusado: ID ou senha do gateway (401)',
  http_402: 'sem crédito no Threema (402)',
  http_404: 'esse ID do Threema não existe (404)',
  http_413: 'mensagem grande demais (413)',
  http_500: 'Threema fora do ar (500)',
  http_502: 'Threema fora do ar (502)',
  http_503: 'Threema fora do ar (503)',
  sem_destinatario_valido: 'ninguém válido para receber',
  threema_recipients_missing: 'ninguém para receber',
  threema_gateway_id_missing: 'remetente sem ID configurado no servidor',
  threema_gateway_secret_missing: 'remetente sem senha configurada no servidor',
  threema_contexto_desativado: 'canal desativado no servidor',
  threema_contexto_desconhecido: 'remetente desconhecido',
  threema_contexto_missing: 'envio sem remetente definido',
  threema_context_channels_invalid: 'mapa de canais do servidor inválido',
  threema_contexto_gateway_id_repetido: 'dois canais com o mesmo remetente',
}
export function fraseErro(e: string | null | undefined): string {
  if (!e) return ''
  if (ERROS[e]) return ERROS[e]
  const http = /^http_(\d{3})$/.exec(e)
  if (http) return `o Threema respondeu erro ${http[1]}`
  // Erro de rede gravado pelo envio ("ConnectTimeout: …", "ReadError: …").
  if (/^[A-Za-z]*(Error|Timeout|Exception)\b/.test(e)) return 'falha de conexão com o Threema'
  return e
}

/** "há 2 h, 3/3" ou "há 2 h, falhou: sem crédito (402)". */
export function ultimoEnvioTexto(
  u: Ultimo | null,
  agora: Date = new Date(),
  e?: Pick<Envio, 'onde_roda' | 'quem_recebe'>,
): string {
  // O Mac só registra aqui quando passar a ler o Cadastro (C13).
  if (!u && e?.onde_roda === 'mac' && e.quem_recebe.via === 'mac_env') return 'o Mac ainda não registra aqui'
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

// ─────────────────────────────────────────────── edição (C7)

export type PessoaUsuario = {
  id: string
  nome: string
  email: string
  admin: boolean
  tem_threema: boolean
  valido: boolean
  aviso: string | null
}
export type GrupoOut = {
  id: string
  nome: string
  descricao: string | null
  membros: RefOut[]
  usado_em: { chave?: string; nome: string; regra?: string | null }[]
}
export type ContatoOut = {
  id: string
  nome: string
  threema_id: string
  ativo: boolean
  origem: string
  observacao: string | null
  // `legado: true` = lista da configuração antiga, onde o ID recebe hoje
  usado_em: { chave?: string; grupo?: string; nome: string; regra?: string | null; legado?: boolean }[]
  e_de_usuario: string | null
}
export type Pessoas = { usuarios: PessoaUsuario[]; grupos: GrupoOut[]; contatos: ContatoOut[] }
export type RefSel = { tipo: 'usuario' | 'grupo' | 'contato'; id: string; nome: string }

/** A confirmação ao desligar, com o que acontece com os avisos. */
export function textoConfirmarDesligar(e: Pick<Envio, 'nome' | 'ao_desligar' | 'ao_desligar_texto'>): string {
  const efeito = e.ao_desligar_texto
    ? `Enquanto estiver desligado, ${e.ao_desligar_texto}.`
    : 'Enquanto estiver desligado, este aviso não sai.'
  return `Desligar "${e.nome}"?\n${efeito}`
}

/** "Se desligar: os avisos deste período se perdem." (o que ACONTECERIA; não
 *  é o estado de agora). '' quando não há o que dizer. */
export function textoSeDesligar(e: Pick<Envio, 'desligavel' | 'ao_desligar_texto'>): string {
  if (!e.desligavel || !e.ao_desligar_texto) return ''
  return `Se desligar: ${e.ao_desligar_texto}.`
}

/** "Mandar '[TESTE] nome' para A, B?" */
export function textoConfirmarTeste(e: Pick<Envio, 'nome' | 'quem_recebe'>): string {
  const nomes = e.quem_recebe.nomes.map((n) => n.nome).join(', ')
  return `Mandar "[TESTE] ${e.nome}" pelo Threema agora para: ${nomes}?`
}

// Mesmo formato do servidor (services/threema._THREEMA_ID).
const THREEMA_ID = /^(?:[A-Z0-9]{8}|\*[A-Z0-9]{7})$/
export function normalizarThreema(v: string | null | undefined): string {
  return (v || '').trim().toUpperCase()
}
export function threemaIdValido(v: string | null | undefined): boolean {
  return THREEMA_ID.test(normalizarThreema(v))
}

export type EnviosDaPessoa = {
  usuario: { id: string; nome: string; tem_threema: boolean }
  envios: {
    // via: 'legado' (configuração antiga: a lista guarda o ID), 'cadastro'
    // (lê daqui) ou 'mac_env' (o Mac ainda usa a lista dele; a daqui não vale)
    chave: string; nome: string; painel: string; onde_roda: string; via: string; unico: boolean; grupo: string | null
    // item 1 na configuração antiga: as regras em que a pessoa está no 👤
    regras?: string[]
    // configuração antiga: onde a lista é editada hoje (onde tirar o ID)
    onde?: string | null
  }[]
  total: number
  unicos: number
  pela_configuracao_antiga: number
  // envios do Mac que ainda usam a lista de lá (o DaVinci não sabe quem está)
  envios_do_mac_no_env?: number
}

/** O aviso de Usuários antes de suspender, excluir ou mexer no Threema de
 *  alguém. null = a pessoa não recebe envio nenhum (não precisa perguntar).
 *
 *  O texto vai por via: na configuração antiga a lista guarda o ID — ela
 *  CONTINUA recebendo depois de suspender ou de apagar o Threema dela aqui
 *  (só sai tirando o ID de lá); "única pessoa" só existe no que já lê o
 *  Cadastro. */
export function textoAvisoPessoa(r: EnviosDaPessoa, acao: string): string | null {
  const antigas = r.envios.filter((e) => e.via === 'legado')
  const daqui = r.envios.filter((e) => e.via === 'cadastro')
  if (!antigas.length && !daqui.length) return null
  const total = antigas.length + daqui.length
  const linhas = [`${r.usuario.nome} recebe ${total} ${total === 1 ? 'envio' : 'envios'} do Threema.`]
  const unicos = daqui.filter((e) => e.unico)
  if (unicos.length) {
    linhas.push(
      `Em ${unicos.length} ela é a única da lista: esses avisos deixam de chegar a qualquer pessoa — `
      + `${listaDeUsos(unicos)}.`,
    )
  }
  if (antigas.length) {
    const curta = acao.replace(/ \(desabilitar\)/, '').replace(/ (esta pessoa|este usuário)$/, '')
    const efeito = /^trocar/.test(acao)
      ? 'o ID antigo dela continua nessas listas: os avisos seguem indo para ele, não para o novo'
      : `o ID dela continua nessas listas e ela CONTINUA recebendo (${curta} aqui não tira)`
    const onde = resumirLugares(antigas.map((e) => e.onde).filter((x): x is string => !!x))
    const lugares = onde.slice(0, 8).join('; ') + (onde.length > 8 ? `; e mais ${onde.length - 8} lugares` : '')
    linhas.push(
      `${antigas.length} ${antigas.length === 1 ? 'usa' : 'usam'} a configuração antiga `
      + `(${listaDeUsos(antigas, 4)}): ${efeito}.`
      + (lugares ? ` Para tirar: ${lugares}.` : ''),
    )
  }
  if (r.envios_do_mac_no_env) {
    linhas.push(
      `Fora isso, ${r.envios_do_mac_no_env} envios do Mac ainda usam a lista do próprio Mac: o DaVinci não sabe se ela está lá.`,
    )
  }
  linhas.push(`A lista completa: Cadastros › Envios Threema › "Só os envios de ${r.usuario.nome}".`)
  linhas.push(`Quer mesmo ${acao}?`)
  return linhas.join('\n')
}

// "Onde tirar" sem repetir 11 vezes "Ouvidoria › Robôs › X › Avisar": os
// robôs viram uma linha, e as listas fixas do servidor, outra.
const ROBOS = 'Ouvidoria › Robôs › '
const FIXA = /^lista fixa no servidor, só o técnico muda \(\.env do servidor: (.+)\)$/
function resumirLugares(onde: string[]): string[] {
  const unicos = [...new Set(onde)]
  const robos = unicos.filter((o) => o.startsWith(ROBOS))
  const fixas = unicos.map((o) => FIXA.exec(o)?.[1]).filter((x): x is string => !!x)
  const out = unicos.filter((o) => !o.startsWith(ROBOS) && !FIXA.test(o))
  if (robos.length === 1) out.push(robos[0])
  else if (robos.length > 1) out.push(`${ROBOS}Avisar de ${robos.length} robôs`)
  if (fixas.length) {
    const nomes = [...new Set(fixas.flatMap((x) => x.split(' / ')))]
    out.push(`lista fixa no servidor, só o técnico muda (${nomes.join(', ')})`)
  }
  return out
}

function listaDeUsos(usos: EnviosDaPessoa['envios'], max = 6): string {
  const nomes = usos.slice(0, max).map(nomeDoUso).join(', ')
  return usos.length > max ? `${nomes} e mais ${usos.length - max}` : nomes
}

// Item 1: o nome da regra só quando é uma (o status de uma regra pode ser
// "Cancelado | Não entregue | Retido na alfândega | Interno").
function nomeDoUso(e: EnviosDaPessoa['envios'][number]): string {
  if (!e.regras?.length) return e.nome
  return e.regras.length === 1 ? `${e.nome} (regra ${e.regras[0]})` : `${e.nome} (${e.regras.length} regras)`
}

/** A confirmação ao desativar um contato externo em uso. Na configuração
 *  antiga ele continua recebendo (a lista guarda o ID), mas some do seletor
 *  da Ouvidoria e do Informar — e sai calado da lista quando alguém a salvar. */
export function textoDesativarContato(c: Pick<ContatoOut, 'nome' | 'usado_em'>): string {
  const daqui = c.usado_em.filter((u) => !u.legado)
  const antigas = c.usado_em.filter((u) => u.legado)
  const linhas: string[] = []
  if (daqui.length) linhas.push(`${c.nome} está em ${daqui.length} lista(s): desativado, para de receber por elas.`)
  if (antigas.length) {
    linhas.push(
      `${c.nome} recebe hoje pela configuração antiga (${antigas.map((u) => u.nome).join(', ')}): `
      + 'desativado, continua recebendo por lá, mas some do seletor da Ouvidoria e do Informar '
      + 'e sai da lista na próxima vez que alguém salvá-la.',
    )
  }
  linhas.push('Desativar?')
  return linhas.join('\n')
}

// ─── atalho dos painéis de origem (components/ThreemaQuemRecebe.vue, F2) ───

/** `GET /api/threema/envios/{chave}/quem-recebe`: só os nomes, nunca o ID. */
export type QuemRecebeLeve = {
  chave: string
  nome: string
  painel: string
  migrado: boolean
  ativo: boolean
  via: string
  usa_configuracao_antiga: boolean
  ninguem: boolean
  nomes: string[]
  avisos: string[]
  // Desligado: por quê e quem desliga (o robô em Ouvidoria › Robôs, a chave
  // do .env, o Cadastro). Ausente na API de antes (aba aberta na publicação).
  desligado?: string | null
  // Quem está na lista e receberia (ao religar, se desligado).
  receberia?: string[]
}

/** O envio na tela de Cadastros › Envios Threema (abre a gaveta dele). */
export function linkDoEnvio(chave: string): string {
  return `/envios-threema?envio=${encodeURIComponent(chave)}`
}

/** A linha do atalho: os nomes, o desligado (âmbar) ou o ninguém (vermelho).
 *  O desligado diz quem desliga (o robô, o .env ou o Cadastro, pelo
 *  servidor) e quem receberia ao religar. */
export function situacaoQuemRecebe(q: QuemRecebeLeve): { tipo: 'ok' | 'desligado' | 'ninguem'; texto: string } {
  if (!q.ativo) {
    const porque = q.desligado || 'Desligado em Cadastros › Envios Threema: não sai para ninguém.'
    const lista = q.receberia?.length ? ` Na lista (recebem ao religar): ${q.receberia.join(', ')}.` : ''
    return { tipo: 'desligado', texto: porque + lista }
  }
  if (q.ninguem || !q.nomes.length) return { tipo: 'ninguem', texto: 'Ninguém recebe: este aviso não está saindo.' }
  return { tipo: 'ok', texto: q.nomes.join(', ') }
}

/** O "Enviar agora" do atalho para com o envio desligado ou sem ninguém (o
 *  servidor recusaria com 422). Sem a lista carregada (erro de rede), quem
 *  decide é o servidor. */
export function podeEnviarAgora(q: QuemRecebeLeve | null): boolean {
  return !q || situacaoQuemRecebe(q).tipo === 'ok'
}

// ─── a tela dos robôs da Ouvidoria (pages/ouvidoria/robos.vue, F3/C10a) ───

/** Contatos externos do Cadastro: quem recebe aviso sem login no DaVinci. */
export const LINK_CONTATOS_EXTERNOS = '/envios-threema?aba=contatos'

/** O que a tela dos robôs lê do aviso de cada robô (GET /api/ouvidoria/robos). */
export type RoboThreema = {
  modo: string
  reaviso_horas: number
  threema_destinatarios: { id: string; nome: string }[]
  // `ouvidoria.<robô>` (null no Relatório da Denúncia, que tem envio próprio)
  threema_envio?: string | null
  // o aviso já lê Cadastros › Envios Threema (ausente na API de antes)
  threema_migrado?: boolean
  // cadastro | cache | legado (a configuração antiga: emergência, sem semente)
  threema_via?: string | null
  threema_ativo?: boolean | null
  // chave da config → envio do Cadastro que a governa (só leitura na tela)
  config_no_cadastro?: Record<string, string>
}

/** O aviso deste robô já lê Cadastros › Envios Threema: quem recebe e o
 *  re-aviso só o admin muda, lá. */
export function roboNoCadastro(r: RoboThreema): boolean {
  return !!r.threema_migrado && !!r.threema_envio
}

/** A coluna "Avisa" do robô ligado cujo aviso já lê o Cadastro. */
export function avisaRoboCadastro(r: RoboThreema): { linha1: string; linha2: string; title: string } {
  if (r.threema_ativo === false) {
    return {
      linha1: 'Desligado',
      linha2: 'em Cadastros › Envios Threema',
      title: 'O aviso deste robô está desligado em Cadastros › Envios Threema (só admin liga). '
        + 'As pendências ficam guardadas e saem juntas ao religar.',
    }
  }
  const nomes = (r.threema_destinatarios || []).map((d) => d.nome || d.id)
  const antiga = r.threema_via === 'legado'
  if (!nomes.length) {
    return antiga
      ? { linha1: 'Ninguém', linha2: 'configuração antiga vazia', title: 'Usando a configuração antiga, que está vazia: o aviso não sai.' }
      : {
          linha1: 'Ninguém',
          linha2: 'ninguém válido no Cadastro',
          title: 'Ninguém da lista em Cadastros › Envios Threema tem Threema válido e ativo: o aviso não está saindo (os admins foram avisados).',
        }
  }
  return {
    linha1: `Threema · ${nomes.length} ${nomes.length === 1 ? 'pessoa' : 'pessoas'}`,
    linha2: `re-aviso ${r.reaviso_horas} h`,
    title: `${nomes.join(', ')} (${antiga ? 'configuração antiga' : 'Cadastros › Envios Threema'})`,
  }
}

/** A config que o PATCH dos robôs manda, sem as chaves que o Cadastro
 *  governa (o servidor ignoraria, com aviso). */
export function configSemCadastro<T>(config: Record<string, T>, r: Pick<RoboThreema, 'config_no_cadastro'>): Record<string, T> {
  const fora = r.config_no_cadastro || {}
  return Object.fromEntries(Object.entries(config).filter(([k]) => !(k in fora)))
}
