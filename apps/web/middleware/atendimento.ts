// /atendimento: só quem o /me devolve com `atendimento: true` — admin que está
// em ATENDIMENTO_USUARIOS no .env da api (Eduardo, 30/09/2026: thorfinn e
// heisenberg). Espelha a trava do router (atendimento_restrito).
export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.atendimento !== true) return navigateTo('/403')
})
