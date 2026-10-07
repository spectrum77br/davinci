import { computed } from 'vue'
import { acessoGarantia } from '~/lib/garantias'

// Garantias (07/10/2026): as três permissões do documento (§6) e o CPF, num
// lugar só — a página /garantias, o bloco "Garantia" e o "Vincular à
// garantia" do /atendimento. Independe da trava de só leitura do
// Atendimento: quem tem "Registrar atendimento" vincula mesmo só lendo a caixa.
export function useGarantiaAcesso() {
  const auth = useAuthStore()
  return computed(() => acessoGarantia(auth.user))
}
