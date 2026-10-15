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
// aberta, aba visível, uma vez por abertura) grava `visto_em` para quem mexe,
// em QUALQUER modo de loja (inclusive Observar) e sem ir à plataforma. UMA
// chamada por abertura (`rotaAoAbrir`): o /lida quando o detalhe diz
// `marcar_lida` (ele grava o visto e marca na plataforma) e, senão, o
// POST /api/atendimento/conversas/{id}/visto (só o visto). Quem só lê nunca
// chama nenhum; o Direct do Instagram (`ig:`) não é conversa da Caixa.
// O POST leva `{ ate }` = o `visto_ate` do detalhe que a tela mostrava (a
// chegada da mensagem mais nova): o servidor compara o visto com a CHEGADA da
// mensagem — a que o sync trouxe depois (a pessoa não viu) continua contando.
// COM A CONVERSA ABERTA (15/10/2026, a revisão — no Duoke, a conversa aberta
// fica lida): depois da abertura, cada releitura que traz mensagem nova (o
// `visto_ate` andou), com a aba visível, chama o /visto de novo (`rever`; só
// o /visto, barato — o /lida continua uma vez por abertura). Falhou, a
// próxima releitura tenta de novo.

export const ATRASO_MARCAR_LIDA_MS = 1500

export type DetalheParaLida = {
  conversa: { id: string; nao_lidas?: number | null }
  marcar_lida?: boolean
  // Até onde a tela mostra (15/10/2026): vai no POST como `ate`.
  visto_ate?: string | null
}

/** A tela deve marcar esta conversa como lida (depois do atraso)? */
export function querMarcarLida(d: DetalheParaLida | null | undefined, podeMexer: boolean): boolean {
  if (!d || !podeMexer || d.marcar_lida !== true) return false
  const n = d.conversa?.nao_lidas
  // Sem o número (API antiga): o servidor decide — a rota é idempotente.
  return n === null || n === undefined || n > 0
}

/** A tela grava o "visto no DaVinci" ao abrir esta conversa (15/10/2026)? */
export function querMarcarVisto(d: DetalheParaLida | null | undefined, podeMexer: boolean): boolean {
  const id = d?.conversa?.id
  return !!id && podeMexer && !id.startsWith('ig:')
}

/** Qual rota a abertura chama: o /lida (visto + plataforma), o /visto (só o visto) ou nenhuma. */
export function rotaAoAbrir(d: DetalheParaLida | null | undefined, podeMexer: boolean): 'lida' | 'visto' | null {
  if (querMarcarLida(d, podeMexer)) return 'lida'
  if (querMarcarVisto(d, podeMexer)) return 'visto'
  return null
}

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
  // novo (15/10/2026). Sem ele, só a abertura grava.
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
    timer = relogio.agendar(() => {
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
    void Promise.resolve()
      .then(() => o.marcar(id))
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
    ateGravado = agora
    void Promise.resolve()
      .then(() => rev(id))
      .catch(() => {
        // falhou: a próxima releitura (ou a volta da aba) tenta de novo
        if (aberta === id && ateGravado === agora) ateGravado = antes
      })
  }

  return {
    /** O detalhe da conversa `id` chegou (ao abrir e a cada releitura); `vistoAte` = o `visto_ate` dele. */
    abrir(id: string, querMarcar: boolean, vistoAte?: string | null) {
      if (id !== aberta) {
        parar()
        aberta = id
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
      aberta = null
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
