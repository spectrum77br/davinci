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

export const ATRASO_MARCAR_LIDA_MS = 1500

export type DetalheParaLida = {
  conversa: { id: string; nao_lidas?: number | null }
  marcar_lida?: boolean
}

/** A tela deve marcar esta conversa como lida (depois do atraso)? */
export function querMarcarLida(d: DetalheParaLida | null | undefined, podeMexer: boolean): boolean {
  if (!d || !podeMexer || d.marcar_lida !== true) return false
  const n = d.conversa?.nao_lidas
  // Sem o número (API antiga): o servidor decide — a rota é idempotente.
  return n === null || n === undefined || n > 0
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
  // O POST /lida (quem chama trata a resposta: zera o número na tela).
  marcar: (id: string) => Promise<unknown>
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
    void Promise.resolve()
      .then(() => o.marcar(id))
      .catch(() => {
        // a conversa continua não lida; a próxima abertura tenta de novo
      })
  }

  return {
    /** O detalhe da conversa `id` chegou (ao abrir e a cada releitura). */
    abrir(id: string, querMarcar: boolean) {
      if (id !== aberta) {
        parar()
        aberta = id
        feita = false
      }
      quer = querMarcar
      if (!quer) parar()
      else agendar()
    },
    /** Trocou de conversa, fechou ou desmontou: nada pendente marca. */
    fechar() {
      parar()
      aberta = null
      quer = false
      feita = false
    },
    /** A aba (ou a Caixa) voltou a ficar visível: conta o atraso de novo. */
    retomar() {
      if (visivel()) agendar()
    },
    /** Para os testes e para quem quer saber. */
    estado() {
      return { aberta, quer, feita, pendente: timer !== null }
    },
  }
}
