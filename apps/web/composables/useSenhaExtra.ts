import { onScopeDispose, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

// Senha extra de páginas sensíveis (Empresas). Eduardo, 25/09/2026: "senha
// segura porque tem informações que muita gente não pode ver".
//
// Quem confere de verdade é o SERVIDOR — sem a chave no cabeçalho ele recusa os
// dados, então esta trava não é só visual. A chave vale 15 minutos.
//
// Onde a chave fica (Eduardo, 25/09: "se eu sair preciso que já bloqueie"):
// SÓ na memória da página, nunca no navegador. Por isso tranca de novo ao
// - sair da área (`area`: a lista e a ficha de uma empresa são a mesma área;
//   Cadastros, Lojas, Valuation etc. não são);
// - recarregar a página ou fechar a aba;
// - vencer os 15 minutos, mesmo parado na tela.
//
// Opções (Emissão de Serviço, 30/09/2026):
// - `podeTrancar`: quando os 15 min vencem no meio de um trabalho que não pode
//   ser cortado (notas saindo para a NFE.io), a tela espera ele acabar para
//   trancar. Só para trabalho que termina sozinho — nunca "janela aberta".
// - `antesDeTrancar`: fecha o que precisa fechar direito antes do cadeado
//   (ex.: a janela de envio, que ao fechar mostra o resumo).
// `perto` fica true no último minuto: a página pode oferecer digitar a senha de
// novo SEM trancar (o que está digitado ou aberto não se perde).
export function useSenhaExtra(
  escopo: string,
  caminhoDesbloqueio: string,
  cabecalho: string,
  area: RegExp,
  opcoes: { podeTrancar?: () => boolean; antesDeTrancar?: () => void } = {},
) {
  const { api } = useApi()
  // useState: a mesma chave para a lista e para a ficha (navegar entre elas
  // não pede a senha de novo). Na recarga ela recomeça vazia.
  const token = useState<string | null>(`senha-extra:${escopo}:token`, () => null)
  const vence = useState<number>(`senha-extra:${escopo}:vence`, () => 0)
  const senha = ref('')
  const erro = ref<string | null>(null)
  const desbloqueando = ref(false)

  // Versões anteriores guardavam a chave na aba; limpa o que tiver sobrado.
  function limparAntigo() {
    if (import.meta.server) return
    try {
      sessionStorage.removeItem(`davinci.${escopo}.token`)
      sessionStorage.removeItem(`davinci.${escopo}.token_exp`)
    } catch { /* navegador sem armazenamento: nada a limpar */ }
  }

  /** Confere a chave em memória; devolve true se ainda vale. */
  function iniciar(): boolean {
    limparAntigo()
    if (token.value && Date.now() >= vence.value) trancar()
    return !!token.value
  }

  function headers(): Record<string, string> {
    return token.value ? { [cabecalho]: token.value } : {}
  }

  async function desbloquear(): Promise<boolean> {
    if (!senha.value || desbloqueando.value) return false
    desbloqueando.value = true
    erro.value = null
    try {
      const r = await api<{ token: string; expires_in: number }>(caminhoDesbloqueio, {
        method: 'POST',
        body: { password: senha.value },
      })
      // 30 s de folga: a tela tranca um pouco antes do servidor recusar.
      vence.value = Date.now() + (r.expires_in - 30) * 1000
      token.value = r.token
      return true
    } catch (e: any) {
      erro.value = mensagemDaSenhaExtra(e)
      return false
    } finally {
      // A senha digitada não fica guardada em lugar nenhum.
      senha.value = ''
      desbloqueando.value = false
    }
  }

  function trancar() {
    token.value = null
    vence.value = 0
  }

  // Tranca assim que puder: na hora, ou quando o trabalho em andamento
  // (`podeTrancar`) terminar — confere de novo a cada 2 s.
  function trancarQuandoPuder() {
    if (relogio) clearTimeout(relogio)
    relogio = null
    if (opcoes.podeTrancar && !opcoes.podeTrancar()) {
      relogio = setTimeout(trancarQuandoPuder, 2000)
      return
    }
    opcoes.antesDeTrancar?.()
    trancar()
  }

  // Tranca na hora em que vence, mesmo com a pessoa parada na tela.
  let relogio: ReturnType<typeof setTimeout> | null = null
  const perto = ref(false)
  let relogioAviso: ReturnType<typeof setTimeout> | null = null
  if (!import.meta.server) {
    watch(
      () => (token.value ? vence.value : 0),
      (quando) => {
        if (relogio) clearTimeout(relogio)
        relogio = quando ? setTimeout(trancarQuandoPuder, Math.max(0, quando - Date.now())) : null
        // Aviso do último minuto (renovar zera: `vence` muda e isto roda de novo).
        if (relogioAviso) clearTimeout(relogioAviso)
        perto.value = false
        relogioAviso = quando
          ? setTimeout(() => { perto.value = true }, Math.max(0, quando - 60_000 - Date.now()))
          : null
      },
      { immediate: true },
    )
    onScopeDispose(() => {
      if (relogio) clearTimeout(relogio)
      if (relogioAviso) clearTimeout(relogioAviso)
    })

    // Saiu da área: tranca quando esta página SAI da tela, não no clique.
    // Trancar no clique mostrava o cadeado enquanto a próxima página ainda
    // carregava (o cadeado piscava antes de mudar de aba). Aqui só anota para
    // onde vai; se a navegação for cancelada, a página fica e nada tranca.
    let saindoDaArea = false
    onBeforeRouteLeave((para) => {
      saindoDaArea = !area.test(para.path)
    })
    // onUnmounted (e não antes): a página já saiu, não tem como redesenhar o cadeado.
    onUnmounted(() => {
      if (saindoDaArea) trancar()
    })
  }

  // A chave venceu no meio do uso: o servidor devolve `<escopo>_locked`.
  function eTravamento(e: any): boolean {
    return e?.data?.detail?.code === `${escopo}_locked`
  }

  return {
    token, senha, erro, desbloqueando, perto, iniciar, headers, desbloquear, trancar, trancarQuandoPuder, eTravamento,
  }
}

/** Texto em português para o erro do desbloqueio. Serve também ao Valuation,
 *  que tem o próprio cartão: a contagem de erros é a mesma nas duas telas
 *  (errar 5 vezes em Empresas trava o Valuation também, é a mesma senha). */
export function mensagemDaSenhaExtra(e: any): string {
  const code = e?.data?.detail?.code
  return code === 'wrong_password' ? 'Senha incorreta.'
    : code === 'muitas_tentativas' ? 'Muitas tentativas erradas. Espere 15 minutos e tente de novo.'
    : code === 'senha_nao_configurada' ? 'A senha desta página ainda não foi configurada.'
    : (code || e?.message || 'erro')
}
