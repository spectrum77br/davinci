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
})
