export default defineNuxtRouteMiddleware(() => {
  const auth = useAuthStore()
  if (auth.user?.role !== 'admin') {
    return navigateTo('/403')
  }
})
