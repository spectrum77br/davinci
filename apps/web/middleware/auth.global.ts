export default defineNuxtRouteMiddleware(async (to) => {
  const auth = useAuthStore()
  if (!auth.fetched) {
    await auth.fetchMe()
  }

  const publicRoutes = ['/login', '/pending-approval', '/privacidade']
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
  // AND no other resource granted via permissions. The lock is meant
  // for "pure operators" — ground-floor users who only ever see the
  // /controle-estoque planilha. The moment the admin grants ANY other
  // permission (margem.view, tabela_precos.view, etc.) the user
  // becomes a supervisor and the lock no longer applies — they get
  // their tags inside /controle-estoque AND can navigate to the other
  // granted pages. The sidebar mirrors this decision so the menu also
  // expands for supervisors (see isOperator in AppSidebar.vue).
  const perms = (auth.user?.permissions ?? {}) as Record<string, { view?: boolean; edit?: boolean; delete?: boolean } | undefined>
  const hasOtherGrant = Object.entries(perms).some(
    ([key, val]) =>
      key !== 'controle_estoque'
      && Boolean(val?.view || val?.edit || val?.delete),
  )
  if (
    auth.isAuthenticated
    && auth.user?.status === 'active'
    && auth.user.role !== 'admin'
    && (auth.user.stock_tags?.length ?? 0) > 0
    && !hasOtherGrant
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
      : '') as string
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
