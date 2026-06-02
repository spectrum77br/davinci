// Restringe a rota ao dono da conta (spectrum77). Espelha o require_owner do
// backend (settings.owner_open_id = "email:spectrum77@tuta.com").
const OWNER_EMAIL = 'spectrum77@tuta.com'

export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.email !== OWNER_EMAIL) return navigateTo('/403')
})
