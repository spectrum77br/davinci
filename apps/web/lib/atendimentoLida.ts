// Marcar a conversa como LIDA ao abrir (14/10/2026) — o Eduardo: "ao passar
// também tem que marcar como visto, igual lá no Duoke quando clicar na
// conversa".
//
// A tela (AtendimentoConversa) chama POST /api/atendimento/conversas/{id}/lida
// quando a PESSOA abre a conversa, e só quando:
//   - o detalhe diz `marcar_lida: true` — o servidor confere as mesmas travas
//     da rota: quem vê mexe na caixa, a loja está num modo em que o DaVinci
//     responde (auto, copiloto ou humano; NUNCA observar), o envio está
//     ligado, a plataforma tem "lida" (chat da Shopee e da TikTok, pós-venda
//     do ML) e há não lidas (services/atendimento/lida.py);
//   - quem vê pode mexer (`canEdit`; quem só lê nunca chama);
//   - a conversa ficou ABERTA por ATRASO_MARCAR_LIDA_MS com a aba visível:
//     quem só passou pela lista com as setas não marca nada. Aba escondida (ou
//     a Caixa atrás de outra aba da página): espera voltar e conta de novo;
//   - uma vez por abertura: o polling de 15 s relê o detalhe, e nem ele nem
//     uma falha da plataforma fazem chamar de novo. Fechou e abriu de novo,
//     é outra abertura.
// Falha (rede, 409, a plataforma recusou) não quebra a tela: a conversa
// continua não lida, e a próxima abertura tenta de novo. A IA, os crons e os
// robôs nunca marcam — só esta tela, pela pessoa.
//
// VISTO NO DAVINCI (15/10/2026, o dono: os números da Caixa batem com o
// Duoke — e lá abrir a conversa zera o número dela): o MESMO gatilho (1,5 s
// aberta, aba visível, uma vez por abertura) grava `visto_em` em qualquer
// modo de loja (inclusive Observar) e sem ir à plataforma — desde 16/11/2026,
// só FORA do número (abaixo). UMA chamada por abertura (`rotaAoAbrir`): o
// /lida quando o detalhe diz `marcar_lida` (marca na plataforma) e, senão, o
// POST /api/atendimento/conversas/{id}/visto (só o visto). Quem só lê chama
// só o /visto (28/10/2026: abrir apagava a notificação para todos — desde
// 16/11/2026, só fora do número); o Direct do Instagram (`ig:`) não é
// conversa da Caixa.
// O POST leva `{ ate }` = o `visto_ate` do detalhe que a tela mostrava (a
// chegada da mensagem mais nova): o servidor compara o visto com a CHEGADA da
// mensagem — a que o sync trouxe depois (a pessoa não viu) continua contando.
// COM A CONVERSA ABERTA (15/10/2026, a revisão): depois da abertura, cada
// releitura que traz mensagem nova (o `visto_ate` andou), com a aba visível,
// chama o /visto de novo (`rever`; só o /visto — o /lida continua uma vez por
// abertura). Falhou, a próxima releitura tenta de novo.
//
// ABRIR NÃO APAGA MAIS A NOTIFICAÇÃO DO NÚMERO (16/11/2026) — o dono: "a
// notificacao agr so vai sair se responder no chat ou se clicar em um botao n
// precisa de resposta q bota do lado do enviar msg ali". Na conversa do NÚMERO
// da Caixa (`doNumero`: o servidor manda `numero_duoke` como número — o chat,
// o pós-venda e a reclamação do ML, a Amazon, a Magalu, a Temu, o
// AliExpress, o Zap) a abertura NÃO grava o visto: nem o /visto ao abrir, nem
// o `rever` com a conversa aberta. A bolinha só sai com a resposta no chat (a
// de pessoa pelo DaVinci, a de fora que o sync traz, a da IA que saiu
// sozinha) ou com o "Não precisa de resposta" do lado do Enviar
// (AtendimentoConversa; o PATCH com `sem_resposta_necessaria`). O /lida
// continua indo ao abrir, mas só marca na PLATAFORMA (o servidor não grava o
// visto do número). Continuam como estão: o "Zerar não lidas" em lote, fechar
// a conversa, a janela de 7 dias e, fora do número, o e-mail do site e a
// pergunta do ML — nelas abrir ainda grava o visto, como antes.

export const ATRASO_MARCAR_LIDA_MS = 1500

export type DetalheParaLida = {
  // `numero_duoke` (16/11/2026): número só nas conversas do número da Caixa;
  // null (ou ausente) fora dele — o e-mail, a pergunta do ML, a avaliação.
  conversa: { id: string; nao_lidas?: number | null; canal?: string | null; numero_duoke?: number | null }
  marcar_lida?: boolean
  // Até onde a tela mostra (15/10/2026): vai no POST como `ate`.
  visto_ate?: string | null
}

/** A conversa é do NÚMERO da Caixa (a bolinha que soma na loja)? O servidor
 *  manda o `numero_duoke` como número só nelas (`pendente.do_numero`). */
export function doNumero(d: { conversa?: { numero_duoke?: number | null } | null } | null | undefined): boolean {
  return typeof d?.conversa?.numero_duoke === 'number'
}

/** A tela deve marcar esta conversa como lida (depois do atraso)? */
export function querMarcarLida(d: DetalheParaLida | null | undefined, podeMexer: boolean): boolean {
  if (!d || !podeMexer || d.marcar_lida !== true) return false
  // Zap (26/10/2026, o tique azul): a Meta não manda contador de não lidas
  // (fica 0); o `marcar_lida` do servidor já diz se há mensagem do cliente
  // ainda não marcada (lida.py `_wamid_para_marcar`).
  if (d.conversa?.canal === 'zap') return true
  const n = d.conversa?.nao_lidas
  // Sem o número (API antiga): o servidor decide — a rota é idempotente.
  return n === null || n === undefined || n > 0
}

/** A tela grava o "visto no DaVinci" ao abrir esta conversa (15/10/2026)?
 *  Quem só lê também (28/10/2026) — só o /visto; o /lida continua de quem mexe.
 *  Nunca na conversa do número (16/11/2026: abrir não apaga a notificação dela)
 *  nem no Direct. */
export function querMarcarVisto(d: DetalheParaLida | null | undefined): boolean {
  const id = d?.conversa?.id
  return !!id && !id.startsWith('ig:') && !doNumero(d)
}

/** Qual rota a abertura chama: o /lida (marca na plataforma), o /visto (só o
 *  visto) ou nenhuma. Na conversa do número (16/11/2026): o /lida ou nenhuma —
 *  e o /lida dela só marca na plataforma (o servidor não grava o visto). */
export function rotaAoAbrir(d: DetalheParaLida | null | undefined, podeMexer: boolean): 'lida' | 'visto' | null {
  if (querMarcarLida(d, podeMexer)) return 'lida'
  if (querMarcarVisto(d)) return 'visto'
  return null
}

// ─── "Não precisa de resposta" do lado do Enviar (16/11/2026) ───────────────
// A outra saída da bolinha (a primeira é responder). A regra e o corpo do
// PATCH /api/atendimento/conversas/{id} moram aqui; o botão, no rodapé do
// AtendimentoConversa (e embaixo do painel "O que a IA responderia", onde não
// há Enviar).

export type ConversaParaNaoPrecisa = {
  numero_duoke?: number | null
  situacao?: string | null
  falta_responder?: boolean | null
  aguardando_resposta?: boolean | null
  sem_resposta_necessaria?: boolean | null
  somente_leitura?: boolean | null
  plataforma?: string | null
  canal?: string | null
}

/** O botão aparece nesta conversa? Só para quem responde (`podeMexer`), fora do
 *  Direct (só leitura), do e-mail e da aba que responde por OUTRA conversa; só na
 *  conversa do número que não está fechada e que tem o que tirar (a bolinha, o
 *  "Falta responder", a vez da loja) — ou que já está marcada (vira "Precisa de
 *  resposta", o desfazer). Vale com a caixa travada (bloqueada, reclamação sem
 *  destino): ali é a única saída além de fechar. */
export function mostraNaoPrecisa(
  c: ConversaParaNaoPrecisa | null | undefined,
  o: { podeMexer: boolean; respondePorOutra?: boolean; ehEmail?: boolean },
): boolean {
  if (!c || !o.podeMexer || c.somente_leitura || o.respondePorOutra || o.ehEmail) return false
  if (typeof c.numero_duoke !== 'number' || c.situacao === 'fechada') return false
  return c.numero_duoke > 0 || c.falta_responder === true || c.aguardando_resposta === true || c.sem_resposta_necessaria === true
}

/** O corpo do PATCH. Marcar leva `ate` = o `visto_ate` do detalhe que a tela
 *  mostrava: chegou mensagem do comprador depois dele, o servidor recusa (409
 *  `chegou_mensagem`). Sem `visto_ate`, vai sem ele. Desfazer: só o `false` (a
 *  bolinha volta). */
export function corpoNaoPrecisa(marcar: boolean, ate?: string | null): { sem_resposta_necessaria: boolean; ate?: string } {
  if (!marcar) return { sem_resposta_necessaria: false }
  return ate ? { sem_resposta_necessaria: true, ate } : { sem_resposta_necessaria: true }
}

// O 409 do PATCH quando chegou mensagem depois do `ate`: a tela mostra a frase
// do servidor e relê a conversa (a mensagem nova aparece).
export const CODIGO_CHEGOU_MENSAGEM = 'chegou_mensagem'

/** A reclamação do ML (no número): marcar tira a bolinha, mas o prazo do ML
 *  continua correndo — pede confirmação. No chat, nenhuma (desfaz com "Precisa
 *  de resposta"). */
export function perguntaNaoPrecisa(c: ConversaParaNaoPrecisa | null | undefined): string | null {
  if (c?.plataforma !== 'ml' || c?.canal !== 'reclamacao') return null
  return 'Marcar a reclamação como "não precisa de resposta"?\n\nA notificação sai daqui, mas o prazo do Mercado Livre continua correndo: se o comprador ou o mediador esperam a loja, responda pela reclamação.'
}

// O title do botão (16/11/2026): o que ele faz e quando a notificação volta.
export const TITULO_NAO_PRECISA = 'Tira a notificação sem responder; volta sozinha se o cliente escrever de novo'

export type RelogioLida = {
  agendar: (fn: () => void, ms: number) => unknown
  cancelar: (t: unknown) => void
}

const RELOGIO_PADRAO: RelogioLida = {
  agendar: (fn, ms) => setTimeout(fn, ms),
  cancelar: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
}

export type OpcoesMarcadorLida = {
  // O POST /lida ou /visto (quem chama escolhe a rota e trata a resposta).
  marcar: (id: string) => Promise<unknown>
  // Com a conversa aberta, a releitura trouxe mensagem nova: o POST /visto de
  // novo (15/10/2026). Sem ele, só a abertura grava. Quem chama decide se
  // grava: na conversa do número, nada (16/11/2026, `querMarcarVisto`).
  rever?: (id: string) => Promise<unknown>
  // A conversa aberta AGORA (a prop): a de quando o relógio dispara tem de ser a mesma.
  atual: () => string | null
  // Aba do navegador visível e a Caixa na frente.
  visivel?: () => boolean
  atrasoMs?: number
  relogio?: RelogioLida
}

export function criarMarcadorDeLida(o: OpcoesMarcadorLida) {
  const atraso = o.atrasoMs ?? ATRASO_MARCAR_LIDA_MS
  const relogio = o.relogio ?? RELOGIO_PADRAO
  const visivel = o.visivel ?? (() => typeof document === 'undefined' || document.visibilityState === 'visible')
  // A abertura de agora: a conversa, se é para marcar e se já foi (ou está indo).
  let aberta: string | null = null
  let geracaoAberta: string | null = null
  let sequenciaAbertura = 0
  let quer = false
  let feita = false
  let timer: unknown = null
  // O `visto_ate` do detalhe de agora e o do último POST (abertura ou `rever`).
  let ate: string | null = null
  let ateGravado: string | null = null

  function parar() {
    if (timer !== null) relogio.cancelar(timer)
    timer = null
  }
  function agendar() {
    if (timer !== null || feita || !quer || !aberta) return
    const id = aberta
    const sequencia = sequenciaAbertura
    timer = relogio.agendar(() => {
      if (sequenciaAbertura !== sequencia) return
      timer = null
      disparar(id)
    }, atraso)
  }
  function disparar(id: string) {
    if (feita || !quer || aberta !== id || o.atual() !== id) return
    // Escondida: espera o `retomar` (aba visível de novo) e conta o atraso de novo.
    if (!visivel()) return
    feita = true
    ateGravado = ate
    const geracao = geracaoAberta
    const sequencia = sequenciaAbertura
    void Promise.resolve()
      .then(() => {
        if (sequenciaAbertura !== sequencia || aberta !== id || geracaoAberta !== geracao || o.atual() !== id || !quer) return
        if (!visivel()) {
          feita = false
          ateGravado = null
          return
        }
        return o.marcar(id)
      })
      .catch(() => {
        // a conversa continua não lida; a próxima abertura tenta de novo
      })
  }
  // Com a conversa aberta (a abertura já gravou), a releitura trouxe mensagem nova.
  function rever() {
    const rev = o.rever
    if (!rev || !feita || !quer || !aberta || o.atual() !== aberta || !visivel()) return
    if (ate === null || ate === ateGravado) return
    const id = aberta
    const antes = ateGravado
    const agora = ate
    const geracao = geracaoAberta
    const sequencia = sequenciaAbertura
    ateGravado = agora
    void Promise.resolve()
      .then(() => {
        if (sequenciaAbertura !== sequencia || aberta !== id || geracaoAberta !== geracao || o.atual() !== id || !quer) return
        if (!visivel()) {
          if (ateGravado === agora) ateGravado = antes
          return
        }
        return rev(id)
      })
      .catch(() => {
        // falhou: a próxima releitura (ou a volta da aba) tenta de novo
        if (sequenciaAbertura === sequencia && aberta === id && geracaoAberta === geracao && ateGravado === agora) ateGravado = antes
      })
  }

  return {
    /** O detalhe da conversa `id` chegou (ao abrir e a cada releitura); `vistoAte` = o `visto_ate` dele. */
    abrir(id: string, querMarcar: boolean, vistoAte?: string | null, geracao?: string | null) {
      const novaGeracao = geracao ?? null
      if (id !== aberta || novaGeracao !== geracaoAberta) {
        parar()
        sequenciaAbertura++
        aberta = id
        geracaoAberta = novaGeracao
        feita = false
        ate = null
        ateGravado = null
      }
      quer = querMarcar
      if (vistoAte !== undefined) ate = vistoAte ?? null
      if (!quer) parar()
      else {
        agendar()
        rever()
      }
    },
    /** Trocou de conversa, fechou ou desmontou: nada pendente marca. */
    fechar() {
      parar()
      sequenciaAbertura++
      aberta = null
      geracaoAberta = null
      quer = false
      feita = false
      ate = null
      ateGravado = null
    },
    /** A aba (ou a Caixa) voltou a ficar visível: conta o atraso de novo (e revê o que chegou). */
    retomar() {
      if (!visivel()) return
      agendar()
      rever()
    },
    /** Para os testes e para quem quer saber. */
    estado() {
      return { aberta, quer, feita, pendente: timer !== null, ate, ateGravado }
    },
  }
}
