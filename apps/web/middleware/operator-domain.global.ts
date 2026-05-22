// Operator-portal domain lock.
//
// When the request host is gestaoestoque.com (or www.), every path
// outside the controle-estoque flow gets redirected to /controle-estoque.
// This is independent of the role-based operator lock in auth.global.ts:
//
//   * auth.global.ts locks non-admins-with-stock-tags to /controle-estoque
//     on ANY host. (existing behavior)
//   * operator-domain.global.ts locks EVERY user — including admin — to
//     /controle-estoque when the host is gestaoestoque.com.
//
// Caddy already redirects gestaoestoque.com/ → /controle-estoque to spare
// the SPA boot for the root URL; this middleware catches direct hits on
// other paths (typed URL, stale bookmark).
export default defineNuxtRouteMiddleware((to) => {
  const url = useRequestURL()
  const host = url.hostname.toLowerCase()
  if (!host.endsWith('gestaoestoque.com')) return

  const allowedPrefixes = ['/controle-estoque', '/login', '/pending-approval']
  if (allowedPrefixes.some((p) => to.path === p || to.path.startsWith(p + '/'))) return

  return navigateTo('/controle-estoque')
})
