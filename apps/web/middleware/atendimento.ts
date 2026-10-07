// /atendimento: só quem o /me devolve com `atendimento: true`. Fase de
// observação (07/10/2026): toda pessoa ativa (menos o operador de estoque);
// quem MEXE é outra chave (`atendimento_mexe`, ATENDIMENTO_USUARIOS), que a
// página lê. Espelha a trava do router (atendimento_restrito).
export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.atendimento !== true) return navigateTo('/403')
})
