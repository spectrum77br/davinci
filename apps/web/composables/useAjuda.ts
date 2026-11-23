// Ajuda por tela (M-18, 23/11/2026): o ícone "?" da barra do topo abre um
// painel à direita com o texto de ajuda da tela aberta. Cada página registra a
// sua com `useAjuda(...)`; o "?" só aparece quando a tela tem ajuda, e ela
// some ao sair da página. Estado único via useState (como o useTelaCheia):
// a página escreve, a barra do topo (AppTopbar) e o painel (AppAjudaPainel) leem.
//
// O texto é só texto (sem HTML): seções com título e itens; um item pode ter um
// termo em negrito na frente ("Excel" — o que ele faz).
import { onScopeDispose, toRaw, toValue, watch, type MaybeRefOrGetter } from 'vue'

export type AjudaItem = string | { termo: string; texto: string }

export interface AjudaSecao {
  titulo: string
  itens: AjudaItem[]
  /** Lista numerada (passo a passo). */
  numerada?: boolean
}

export interface Ajuda {
  /** Nome da tela: o painel mostra "Ajuda · <titulo>". */
  titulo: string
  conteudo: AjudaSecao[]
}

/** A ajuda da tela aberta (null = a tela não tem ajuda, o "?" some). */
export function useAjudaAtual() {
  return useState<Ajuda | null>('app:ajuda', () => null)
}

/** O painel de ajuda está aberto? */
export function useAjudaAberta() {
  return useState<boolean>('app:ajudaAberta', () => false)
}

/**
 * A página registra a sua ajuda. Aceita um valor, ref ou função: quando vira
 * null (ex.: outra aba da mesma página) a ajuda sai. Ao sair da página também.
 * Só apaga se a ajuda da tela ainda for a desta página — a próxima página pode
 * já ter registrado a dela antes desta desmontar.
 */
export function useAjuda(ajuda: MaybeRefOrGetter<Ajuda | null | undefined>) {
  // Só no navegador: no servidor a barra do topo é desenhada antes da página, e o
  // "?" sairia só no navegador (o Vue reclama da diferença ao hidratar).
  if (!import.meta.client) return
  const atual = useAjudaAtual()
  const aberta = useAjudaAberta()
  let minha: Ajuda | null = null

  function soltar() {
    if (minha && toRaw(atual.value) === minha) {
      atual.value = null
      aberta.value = false
    }
    minha = null
  }

  watch(
    () => toValue(ajuda),
    (nova) => {
      soltar()
      if (nova) {
        minha = toRaw(nova)
        atual.value = minha
      }
    },
    { immediate: true },
  )
  onScopeDispose(soltar)
}
