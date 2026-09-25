import { ref } from 'vue'

// Senha extra de páginas sensíveis (Valuation, Empresas). Eduardo, 25/09/2026:
// "senha segura porque tem informações que muita gente não pode ver".
//
// A chave vem do POST de desbloqueio e fica em sessionStorage: some ao fechar a
// aba e vale 15 minutos. Quem confere de verdade é o SERVIDOR — sem a chave no
// cabeçalho ele recusa os dados, então esta trava não é só visual.
export function useSenhaExtra(escopo: string, caminhoDesbloqueio: string, cabecalho: string) {
  const { api } = useApi()
  const CHAVE = `davinci.${escopo}.token`
  const VENCE = `davinci.${escopo}.token_exp`

  const token = ref<string | null>(null)
  const senha = ref('')
  const erro = ref<string | null>(null)
  const desbloqueando = ref(false)

  function lerGuardado(): string | null {
    if (import.meta.server) return null
    const tok = sessionStorage.getItem(CHAVE)
    const exp = Number(sessionStorage.getItem(VENCE) || 0)
    if (!tok || !exp || Date.now() / 1000 > exp) {
      sessionStorage.removeItem(CHAVE)
      sessionStorage.removeItem(VENCE)
      return null
    }
    return tok
  }

  function iniciar() {
    token.value = lerGuardado()
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
      token.value = r.token
      sessionStorage.setItem(CHAVE, r.token)
      // 30 s de folga: a tela tranca um pouco antes do servidor recusar.
      sessionStorage.setItem(VENCE, String(Math.floor(Date.now() / 1000) + r.expires_in - 30))
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
    if (!import.meta.server) {
      sessionStorage.removeItem(CHAVE)
      sessionStorage.removeItem(VENCE)
    }
    token.value = null
  }

  // A chave venceu no meio do uso: o servidor devolve `<escopo>_locked`.
  function eTravamento(e: any): boolean {
    return e?.data?.detail?.code === `${escopo}_locked`
  }

  return { token, senha, erro, desbloqueando, iniciar, headers, desbloquear, trancar, eTravamento }
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
