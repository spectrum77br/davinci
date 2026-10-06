// /app-uranyx/*: só quem o /me devolve com `app_uranyx: true` — admin cujo
// e-mail está em APP_URANYX_USUARIOS no .env da api (Eduardo, 06/10/2026:
// thorfinn e heisenberg). Espelha a trava da API (app_uranyx_restrito).
export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.app_uranyx !== true) return navigateTo('/403')
})
