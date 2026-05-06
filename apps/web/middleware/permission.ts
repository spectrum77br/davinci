import type { Action, Resource } from '~/composables/useCan'

export default defineNuxtRouteMiddleware((to) => {
  const meta = (to.meta?.permission as { resource: Resource; action: Action } | undefined)
  if (!meta) return
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.role === 'admin') return
  const ok = u.permissions?.[meta.resource]?.[meta.action] === true
  if (!ok) return navigateTo('/403')
})
