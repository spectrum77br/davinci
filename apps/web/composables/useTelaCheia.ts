// "Tela cheia" de uma página (03/11/2026, o Atendimento: "deixar fullscreen só
// o atendimento, parecido em dimensão com o Duoke") — estado único via Nuxt's
// useState: a página liga, o layout `default` esconde o menu lateral e a barra
// do topo e tira o respiro do <main>. Quem liga desliga ao sair da página.

export function useTelaCheia() {
  return useState<boolean>('app:telaCheia', () => false)
}
