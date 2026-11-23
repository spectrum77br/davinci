// Produtos 2 e Pedidos 2 (tabelas do DaVinci ERP dentro do DaVinci): só quem o /me devolve com
// `erp: true`, o mesmo portão do /erp (services/erp_acesso; 23/11/2026: thorfinn, heisenberg e
// harry potter). A API do ERP também recusa os outros no Caddy (forward_auth).
export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.erp !== true) return navigateTo('/403')
})
