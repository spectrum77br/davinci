export default defineNuxtRouteMiddleware(async (to) => {
  const auth = useAuthStore()
  if (!auth.fetched) {
    await auth.fetchMe()
  }

  const publicRoutes = ['/login', '/pending-approval']
  const isPublic = publicRoutes.includes(to.path)

  if (!auth.isAuthenticated && !isPublic) {
    return navigateTo(`/login?next=${encodeURIComponent(to.fullPath)}`)
  }

  if (auth.isAuthenticated && to.path === '/login') {
    return navigateTo('/')
  }

  if (auth.isAuthenticated && auth.user?.status === 'pending' && to.path !== '/pending-approval') {
    return navigateTo('/pending-approval')
  }

  if (auth.isAuthenticated && auth.user?.status === 'active' && to.path === '/pending-approval') {
    return navigateTo('/')
  }

  // Operador de estoque: role != admin AND at least one stock_tag set
  // → locked to /controle-estoque. Any other route bounces back.
  // The sidebar also hides every other item for these users, but the
  // guard is the authoritative line — a typed URL or stale bookmark
  // still gets caught here.
  if (
    auth.isAuthenticated
    && auth.user?.status === 'active'
    && auth.user.role !== 'admin'
    && (auth.user.stock_tags?.length ?? 0) > 0
    && to.path !== '/controle-estoque'
    && !to.path.startsWith('/login')
  ) {
    return navigateTo('/controle-estoque')
  }

  // Redirect to onboarding if no integrations yet. Only on the root page —
  // we don't trap users inside other pages they navigated to deliberately.
  if (
    auth.isAuthenticated
    && auth.user?.status === 'active'
    && to.path === '/'
  ) {
    const config = useRuntimeConfig()
    const base = (import.meta.server
      ? (config as any).apiUrlInternal
      : config.public.apiUrl) as string
    try {
      const r = await $fetch<{ needs_onboarding: boolean }>(`${base}/api/dashboard`, {
        credentials: 'include',
        headers: import.meta.server ? useRequestHeaders(['cookie']) : undefined,
      })
      if (r.needs_onboarding) {
        return navigateTo('/onboarding')
      }
    } catch {
      // Ignore — dashboard not reachable shouldn't block landing.
    }
  }
})
