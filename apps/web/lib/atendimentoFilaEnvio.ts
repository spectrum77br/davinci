// Fila de envio do comunicador (06/11/2026). O dono, com a tela na mão: "queria
// ajustar a questão que quando envia msg fica carregando ali na mensagem pra
// enviar ao invés de enviar e mostrar carregando ali no comunicador já pra poder
// enviar outra pra entrar na fila".
//
// Ao apertar Enter a mensagem vira um balão na conversa, a caixa limpa na hora e
// a pessoa já escreve a próxima. Aqui ficam as regras puras: cada transição
// recebe a lista de itens de UMA conversa e devolve uma lista nova. Quem guarda
// a fila (por conversa, em memória) e fala com a API é o AtendimentoConversa.
// O backend continua sendo a trava (`uq_atendimento_envio_em_voo`,
// `conversa_mudou`, `envio_repetido`): a fila só organiza a ordem.
//
// O que vale em cada estado:
//   na_fila  : esperando a vez. Nada foi mandado.
//   enviando : o POST está no ar (ou esperando alguns segundos entre as recusas
//              temporárias, sem mostrar erro).
//   falhou   : a plataforma recusou (status `falhou`) ou o servidor recusou com
//              código (409, 422). NADA chegou ao comprador. A fila PARA: a ordem
//              importa, a segunda não sai se a primeira não saiu.
//   incerto  : sem resposta do servidor (rede, 5xx). PODE ter saído. A fila para,
//              a conversa é relida e nunca se reenvia sozinho: é o que evita o
//              comprador receber duas vezes.
//   editando : "Editar" com mensagem esperando atrás. O texto está na caixa e o
//              item só guarda o lugar: as seguintes não passam na frente dele. O
//              próximo Enter entra aqui; "Descartar" solta a fila.

export type EstadoDoItem = 'na_fila' | 'enviando' | 'falhou' | 'incerto' | 'editando'
export type CorpoDoItem = { texto: string; rascunho_id?: string; para?: string }
export type ErroDoItem = { texto: string; motivos: string[] }
export type SugestaoDoItem = { id: string; usada: boolean }

export type ItemFila = {
  // "fila:<n>": o balão é uma pseudo-mensagem na linha do tempo, sem id do banco.
  id: string
  conversaId: string
  texto: string
  // O corpo do POST, sem o `ultima_vista_id` (calculado na hora de sair).
  corpo: CorpoDoItem
  // Só o item que levou a sugestão pergunta "a sugestão estava boa?".
  sugestao: SugestaoDoItem | null
  nome: string
  plataforma: string
  // Os ids das mensagens na tela ao apertar: a linha real deste item é uma que
  // NÃO está aqui. Cresce com as linhas gravadas pelos itens de antes, para duas
  // mensagens com o mesmo texto não se confundirem.
  antes: ReadonlySet<string>
  // A última mensagem real que a pessoa tinha na tela ao apertar.
  vista: string | null
  estado: EstadoDoItem
  erro: ErroDoItem | null
  // A recusa foi "a conversa mudou" (ou confirmável): "Tentar de novo" confirma.
  permiteConfirmar: boolean
  confirmar: boolean
  // Quantas recusas temporárias seguidas este item já esperou.
  tentativas: number
  // "Não saiu" porque a releitura não achou a linha: é a falta de prova, não uma
  // recusa. O POST pode ter chegado e estar esperando a trava da conversa, então as
  // próximas leituras continuam olhando.
  podeAparecer: boolean
  // Desde quando não há resposta do servidor (ms): conta o envio preso.
  incertoDesde: number | null
  criadoEm: string
}

// A mensagem real, só no que a fila olha.
export type MensagemDaFila = { id: string; autor?: string | null; origem?: string | null; texto?: string | null; status?: string | null }
type Normalizar = (texto: string | null | undefined, plataforma: string | null | undefined) => string

// ─── onde vale ──────────────────────────────────────────────────────────────
// Chat de plataforma, pós-venda, reclamação e WhatsApp. Fica como sempre foi, com
// o botão girando e a caixa travada: a pergunta do ML (uma resposta só por
// pergunta, e pública: a segunda vira `sem_pergunta_pendente`; em 05/11 teve 5
// dessas na mesma pergunta), a avaliação (pública) e o e-mail (Ctrl+Enter, "não
// responder", "enviar mesmo assim" e fila própria). Lista branca: canal novo só
// entra aqui de propósito.
export const CANAIS_COM_FILA: ReadonlySet<string> = new Set(['chat', 'pos_venda', 'reclamacao', 'zap'])
export function filaValeNoCanal(canal: string | null | undefined, ehEmail = false): boolean {
  if (ehEmail) return false
  return CANAIS_COM_FILA.has(canal || '')
}

// ─── recusas ────────────────────────────────────────────────────────────────
// Outro envio ou a rodada do sync segurando a conversa: passa sozinho. Tenta de
// novo até 3 vezes, com alguns segundos de intervalo, sem mostrar erro.
export const RECUSAS_TEMPORARIAS: ReadonlySet<string> = new Set(['envio_em_andamento', 'conversa_ocupada'])
export const MAX_RETENTATIVAS = 3
export const INTERVALO_RETENTATIVA_MS = 3000

export type TipoDaFalha = 'temporaria' | 'incerto' | 'recusa'
export function tipoDaFalha(code: string | null | undefined, status: number | null | undefined): TipoDaFalha {
  if (code && RECUSAS_TEMPORARIAS.has(code)) return 'temporaria'
  // Sem resposta que diga o que houve: pode ter saído (a mesma régua do envio de sempre).
  if (!code && (!status || status >= 500)) return 'incerto'
  return 'recusa'
}
export function podeRetentar(item: Pick<ItemFila, 'tentativas'>): boolean {
  return item.tentativas < MAX_RETENTATIVAS
}

// ─── o item e as transições ─────────────────────────────────────────────────
export function novoItem(n: number, dados: {
  conversaId: string
  corpo: CorpoDoItem
  sugestao?: SugestaoDoItem | null
  nome: string
  plataforma: string
  antes: Iterable<string>
  vista: string | null
  confirmar?: boolean
  criadoEm: string
}): ItemFila {
  return {
    id: `fila:${n}`,
    conversaId: dados.conversaId,
    texto: dados.corpo.texto,
    corpo: dados.corpo,
    sugestao: dados.sugestao ?? null,
    nome: dados.nome,
    plataforma: dados.plataforma,
    antes: new Set(dados.antes),
    vista: dados.vista,
    estado: 'na_fila',
    erro: null,
    permiteConfirmar: false,
    confirmar: dados.confirmar === true,
    tentativas: 0,
    podeAparecer: false,
    incertoDesde: null,
    criadoEm: dados.criadoEm,
  }
}
// Entra no fim da fila, ou no lugar que o "Editar" guardou.
export function entrarNaFila(itens: ItemFila[], novo: ItemFila): ItemFila[] {
  return itens[0]?.estado === 'editando' ? [novo, ...itens.slice(1)] : [...itens, novo]
}

function mudar(itens: ItemFila[], id: string, f: (i: ItemFila) => ItemFila): ItemFila[] {
  return itens.map((i) => (i.id === id ? f(i) : i))
}
export function marcarEnviando(itens: ItemFila[], id: string): ItemFila[] {
  return mudar(itens, id, (i) => ({ ...i, estado: 'enviando', erro: null }))
}
// Recusa temporária: conta a tentativa; na tela ele continua "enviando…".
export function marcarRetentativa(itens: ItemFila[], id: string): ItemFila[] {
  return mudar(itens, id, (i) => ({ ...i, tentativas: i.tentativas + 1 }))
}
export function voltarParaFila(itens: ItemFila[], id: string): ItemFila[] {
  return mudar(itens, id, (i) => ({ ...i, estado: 'na_fila' }))
}
// `realId`: a linha que o servidor gravou com `falhou`. Ela não é mais a linha
// deste item (se ele sair de novo, a linha nova é outra) e nunca foi a dos que
// esperam atrás: com o mesmo texto, o seguinte se confundiria com ela.
export function marcarFalhou(
  itens: ItemFila[],
  id: string,
  erro: ErroDoItem,
  opcoes: { permiteConfirmar?: boolean; realId?: string | null; podeAparecer?: boolean } = {},
): ItemFila[] {
  const marcados = mudar(itens, id, (i) => ({
    ...i,
    estado: 'falhou',
    erro,
    permiteConfirmar: opcoes.permiteConfirmar === true,
    podeAparecer: opcoes.podeAparecer === true,
    tentativas: 0,
  }))
  const realId = opcoes.realId
  return realId ? marcados.map((i) => ({ ...i, antes: new Set([...i.antes, realId]) })) : marcados
}
export function marcarIncerto(itens: ItemFila[], id: string, agora: number): ItemFila[] {
  return mudar(itens, id, (i) => ({ ...i, estado: 'incerto', erro: null, tentativas: 0, podeAparecer: false, incertoDesde: i.incertoDesde ?? agora }))
}
// "Editar" com mensagem esperando atrás: o item guarda o lugar enquanto o texto
// está na caixa, para a segunda não sair antes da primeira corrigida.
export function marcarEditando(itens: ItemFila[], id: string): ItemFila[] {
  return mudar(itens, id, (i) => ({ ...i, estado: 'editando', erro: null, permiteConfirmar: false, podeAparecer: false, tentativas: 0 }))
}
// "Tentar de novo": volta para o começo da vez, confirmando se a recusa pedia.
export function paraNovaTentativa(itens: ItemFila[], id: string): ItemFila[] {
  return mudar(itens, id, (i) => ({
    ...i,
    estado: 'na_fila',
    erro: null,
    tentativas: 0,
    confirmar: i.confirmar || i.permiteConfirmar,
    permiteConfirmar: false,
    podeAparecer: false,
    incertoDesde: null,
  }))
}
// O item saiu da fila. `realId`: a linha que ele gravou, que não pode passar pela
// linha dos que vêm depois com o mesmo texto.
export function tirarItem(itens: ItemFila[], id: string, realId?: string | null): ItemFila[] {
  const resto = itens.filter((i) => i.id !== id)
  if (!realId) return resto
  return resto.map((i) => ({ ...i, antes: new Set([...i.antes, realId]) }))
}
// A pessoa tirou o item. Sobrou só o lugar guardado pelo "Editar": sem ninguém
// esperando atrás ele não segura mais nada, e o texto já está na caixa.
export function descartarDaFila(itens: ItemFila[], id: string): ItemFila[] {
  const resto = itens.filter((i) => i.id !== id)
  return resto.length === 1 && resto[0].estado === 'editando' ? [] : resto
}
// A resposta "a conferir" foi marcada "Não saiu" (ou o aviso de entrega disse que
// não saiu) com texto esperando: o item volta para a frente da fila, vermelho, com
// as ações dele. A linha `falhou` não é mais a linha dele; a pergunta da sugestão
// já foi feita quando ele saiu.
export function devolverComoFalhou(itens: ItemFila[], item: ItemFila, erro: ErroDoItem, realId: string): ItemFila[] {
  return [{
    ...item,
    estado: 'falhou',
    erro,
    sugestao: null,
    permiteConfirmar: false,
    podeAparecer: false,
    incertoDesde: null,
    tentativas: 0,
    antes: new Set([...item.antes, realId]),
  }, ...itens]
}

// ─── a vez de cada um ───────────────────────────────────────────────────────
// Um por vez, na ordem em que a pessoa apertou: só a cabeça da fila anda.
export function proximoParaSair(itens: ItemFila[]): ItemFila | null {
  const cabeca = itens[0]
  return cabeca?.estado === 'na_fila' ? cabeca : null
}
export function filaParada(itens: ItemFila[]): boolean {
  const e = itens[0]?.estado
  return e === 'falhou' || e === 'incerto' || e === 'editando'
}

// ─── o que segura a fila sem ser a cabeça dela ──────────────────────────────
// `conferir`: a plataforma não confirmou a resposta (status `revisar`) com texto
// esperando. Fica até a pessoa marcar no balão dela; o item guardado volta se ela
// marcar "Não saiu".
// `anexo`: o envio de anexos que o texto esperava não terminou bem. Fica até o
// anexo sair ou a pessoa mandar o texto mesmo assim.
export type Retencao =
  | { tipo: 'conferir'; mensagemId: string; item: ItemFila }
  | { tipo: 'anexo'; motivo: string }

export function conferidaNaLeitura(mensagemId: string, mensagens: readonly MensagemDaFila[] | null | undefined): 'fica' | 'saiu' | 'nao_saiu' {
  const status = (mensagens || []).find((x) => x.id === mensagemId)?.status
  if (status === 'enviada') return 'saiu'
  return status === 'falhou' ? 'nao_saiu' : 'fica'
}
// O texto esperando só segue se todo anexo previsto saiu e nada ficou para trás
// (recusado, parado no meio, "pode ter saído" ou largado ao trocar de conversa).
export function anexosDeixamSeguir(r: { previstos: number; saidos: number; restantes: number }): boolean {
  return r.saidos >= r.previstos && r.restantes === 0
}
export const MOTIVO_ANEXO_NAO_SAIU = 'O anexo que ia antes desta mensagem não saiu (ou não deu para confirmar). Resolva o anexo, ou mande esta mensagem mesmo assim.'
export const MOTIVO_PARADA_A_CONFERIR = 'A mensagem de cima ficou a conferir: marque no balão dela se saiu. Esta sai depois.'

// O corpo do POST. O `ultima_vista_id` é calculado aqui, na hora de sair: do
// primeiro, a última mensagem real que a pessoa tinha na tela; dos seguintes, a
// que o item anterior acabou de gravar. Assim a própria mensagem anterior não
// vira `conversa_mudou` e a resposta de outra pessoa continua virando.
export function corpoDeSaida(item: ItemFila, gravadaAnterior: string | null | undefined): CorpoDoItem & { ultima_vista_id?: string; confirmar?: boolean } {
  const corpo: CorpoDoItem & { ultima_vista_id?: string; confirmar?: boolean } = { ...item.corpo }
  const vista = gravadaAnterior || item.vista
  if (vista) corpo.ultima_vista_id = vista
  if (item.confirmar) corpo.confirmar = true
  return corpo
}

// ─── o balão e a linha real ─────────────────────────────────────────────────
// A linha real deste item: da loja, escrita por gente no DaVinci, fora de `antes`,
// com o mesmo texto (a mesma comparação do `resolverIncerto` da tela).
export function mensagemRealDoItem<M extends MensagemDaFila>(item: ItemFila, mensagens: readonly M[] | null | undefined, normalizar: Normalizar): M | null {
  const alvo = normalizar(item.texto, item.plataforma)
  return (mensagens || []).find((x) => {
    if (item.antes.has(x.id) || x.autor !== 'loja' || x.origem !== 'davinci_humano') return false
    const t = (x.texto || '').trim()
    return t === alvo || t === item.texto
  }) ?? null
}
// A conversa relida, para o item sem resposta do servidor. A linha `enviando` ainda
// não diz o que houve: o servidor pode estar esperando a plataforma, então o item
// fica até uma leitura trazer o resultado. Sem esse cuidado a fila andaria com a
// primeira ainda no ar e, se ela falhasse, o comprador receberia só a segunda.
// Passou de ENVIO_PRESO_MS (a mesma régua do `ENVIO_PRESO` do servidor): o envio
// morreu no meio e nenhuma leitura vai resolver, a pessoa decide.
export const ENVIO_PRESO_MS = 10 * 60 * 1000
export type DecisaoDaLeitura<M> = { acao: 'nao_achou' } | { acao: 'esperar' | 'presa' | 'concluir'; m: M }
export function decidirPelaLeitura<M extends MensagemDaFila>(item: ItemFila, mensagens: readonly M[] | null | undefined, normalizar: Normalizar, agora: number): DecisaoDaLeitura<M> {
  const m = mensagemRealDoItem(item, mensagens, normalizar)
  if (!m) return { acao: 'nao_achou' }
  if (m.status !== 'enviando') return { acao: 'concluir', m }
  return item.incertoDesde != null && agora - item.incertoDesde >= ENVIO_PRESO_MS ? { acao: 'presa', m } : { acao: 'esperar', m }
}
// O servidor grava a linha ANTES de falar com a plataforma: uma releitura pode
// trazer a mensagem real com o balão ainda na tela. Aí o balão some, para a
// mensagem não aparecer duas vezes. Só quem já foi mandado pode ter linha real: o
// `na_fila` não saiu ainda, e o `falhou` fica no lugar da linha dele.
export function baloesVisiveis(itens: ItemFila[], mensagens: readonly MensagemDaFila[] | null | undefined, normalizar: Normalizar): ItemFila[] {
  return itens.filter((i) => {
    if (i.estado !== 'enviando' && i.estado !== 'incerto') return true
    return !mensagemRealDoItem(i, mensagens, normalizar)
  })
}

export type SeloDoItem = { label: string; cls: string; hint: string }
const AMBAR = 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
export function seloDoItem(item: Pick<ItemFila, 'estado'>, parada: boolean): SeloDoItem {
  if (item.estado === 'enviando') return { label: 'enviando…', cls: AMBAR, hint: 'saindo para a plataforma agora' }
  if (item.estado === 'falhou') return { label: 'não saiu', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'esta mensagem NÃO chegou ao comprador' }
  if (item.estado === 'incerto') return { label: 'pode ter saído', cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300', hint: 'sem resposta do servidor: a tela mostra o que houve assim que a conversa atualizar' }
  if (item.estado === 'editando') return { label: 'em edição', cls: AMBAR, hint: 'o texto está na caixa de resposta: ao enviar, ele sai neste lugar da fila' }
  if (parada) return { label: 'na fila · parada', cls: AMBAR, hint: 'a fila desta conversa parou: resolva o que a segura e esta sai sozinha' }
  return { label: 'na fila', cls: 'bg-muted text-muted-foreground', hint: 'sai assim que a mensagem de cima terminar' }
}

// ─── editar ─────────────────────────────────────────────────────────────────
// "Editar" põe o texto do item na caixa e nunca apaga o que a pessoa escreveu:
// com texto escrito, acrescenta numa linha nova. A sugestão intocada da IA
// (`prefill`) não é escrita da pessoa: o texto do item entra no lugar dela.
export function caixaTemEscrita(atual: string, prefill: string): boolean {
  return !!atual.trim() && atual !== prefill
}
export function textoAoEditar(atual: string, prefill: string, doItem: string): string {
  return caixaTemEscrita(atual, prefill) ? `${atual.replace(/\s+$/, '')}\n${doItem}` : doItem
}
// Reclamação do ML: o texto já escrito é para outro destino. Juntar os dois mandaria
// um deles a quem não devia, então o "Editar" não mexe em nada e avisa.
export function editarMudariaDestino(item: Pick<ItemFila, 'corpo'>, destinoAtual: string | null | undefined, escrita: boolean): boolean {
  return escrita && !!item.corpo.para && !!destinoAtual && item.corpo.para !== destinoAtual
}
// A fila esvaziou. Ir para a aba do destino troca junto o destino da caixa (o watch
// da aba): só com a pessoa ainda no mesmo destino e sem nada escrito.
export function abaSegueODestino(para: string, destinoAtual: string | null | undefined, escrita: boolean): boolean {
  return para === destinoAtual && !escrita
}

// ─── fechar a aba ───────────────────────────────────────────────────────────
// A fila é só memória: fechar com item esperando ou no ar perde a mensagem.
export function temItemVivo(filas: Iterable<ItemFila[]>): boolean {
  for (const itens of filas) {
    if (itens.some((i) => i.estado === 'na_fila' || i.estado === 'enviando')) return true
  }
  return false
}

// O botão de anexo enquanto a fila de texto tem item: fotos e NF saem pelo caminho
// delas, sem fila, e não podem passar na frente do texto.
export const MOTIVO_ANEXO_COM_FILA = 'Há mensagens saindo nesta conversa: espere terminarem (ou resolva a que não saiu) para anexar, para não trocar a ordem.'
