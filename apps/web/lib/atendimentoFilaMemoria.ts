// A memória da fila de envio do comunicador (06/11/2026). As regras estão em
// lib/atendimentoFilaEnvio.ts. Fica fora do componente porque a Caixa desmonta a
// conversa ao voltar para a lista (no celular) e ao trocar de página: a fila
// continua andando, e ao voltar os balões estão lá, com as ações. Só memória: um F5
// ou fechar a aba perde o que não saiu, por isso o `beforeunload` pergunta antes.
//
// Quem anda a fila pode ser uma instância que já desmontou. O que é da tela
// (a linha nova, a releitura, a pergunta da sugestão) vai pelas `telasDaFila`: as
// instâncias montadas agora, e só elas relem a conversa. Reler por uma instância
// morta marcaria como lida uma conversa que a pessoa já fechou.
//
// Sair da conta não recarrega a página, e o módulo fica: `mudouUsuario` limpa a
// memória (plugins/atendimento-fila-usuario.client.ts). Sem isso quem entrasse na
// mesma aba veria os balões, com texto de cliente, e o que estava na fila sairia
// com a sessão dele.
import { reactive, shallowReactive } from 'vue'
import { temItemVivo, type ItemFila, type Retencao, type SugestaoDoItem } from '~/lib/atendimentoFilaEnvio'

export type TelaDaFila<M = any> = {
  aberta: (id: string) => boolean
  visivel: (id: string) => boolean
  mostrarReal: (id: string, m: M) => void
  pedirAvaliacao: (id: string, s: SugestaoDoItem) => void
  reler: (id: string) => Promise<unknown>
  aoEsvaziar: (id: string, para?: string) => Promise<unknown>
}

export function criarMemoriaDaFila() {
  const filas = shallowReactive(new Map<string, ItemFila[]>())
  // As linhas `falhou` que o servidor gravou para um item da fila. Na tela fica o
  // balão vermelho do item, com as ações dele, e não as duas coisas.
  const absorvidas = shallowReactive(new Set<string>())
  // A mensagem que o item anterior acabou de gravar: o "vista" do próximo.
  const gravadasDaFila = new Map<string, string>()
  // A sugestão que já foi com um item: a pergunta "a sugestão estava boa?" é dele,
  // não dos que a pessoa escreveu depois com a mesma sugestão ainda na tela.
  const sugestoesLevadas = new Map<string, string>()
  // O que segura a fila sem ser a cabeça dela (`Retencao`).
  const retencoes = shallowReactive(new Map<string, Retencao>())
  let numero = 0
  let usuario: string | null = null
  return {
    filas,
    absorvidas,
    // As conversas com envio no ar. É o que para o polling e faz o texto esperar um
    // envio de anexos em voo.
    enviandoIds: reactive(new Set<string>()),
    gravadasDaFila,
    sugestoesLevadas,
    retencoes,
    telasDaFila: new Set<TelaDaFila>(),
    novoNumeroDaFila: () => ++numero,
    // Uma função só, para somar e tirar o ouvinte não depender de qual instância.
    avisoAoFechar(e: BeforeUnloadEvent) {
      if (!temItemVivo(filas.values())) return
      e.preventDefault()
      e.returnValue = ''
    },
    // Saiu da conta ou entrou outra pessoa: nada de quem saiu fica na tela, e o que
    // esperava na fila não sai mais. Ficam o `enviandoIds`, porque o POST em voo está
    // mesmo no ar e o dono dele é quem tira de lá (andarFila), e o `numero`, para o id
    // de um item novo nunca repetir o do item em voo.
    mudouUsuario(novo: string | null) {
      if (novo === usuario) return
      usuario = novo
      filas.clear()
      absorvidas.clear()
      gravadasDaFila.clear()
      sugestoesLevadas.clear()
      retencoes.clear()
    },
  }
}

export const memoriaDaFila = criarMemoriaDaFila()
