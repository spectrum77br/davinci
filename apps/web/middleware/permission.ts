import type { Action, Resource } from '~/composables/useCan'

export default defineNuxtRouteMiddleware((to) => {
  // 16/10: `permissionAny` = qualquer uma das permissões serve (Conciliação abre pra quem só tem Margem)
  const qualquer = to.meta?.permissionAny as Array<{ resource: Resource; action: Action }> | undefined
  const meta = (to.meta?.permission as { resource: Resource; action: Action } | undefined)
  if (!meta && !qualquer) return
  const auth = useAuthStore()
  const u = auth.user
  if (!u) return navigateTo('/login')
  if (u.role === 'admin') return
  const tem = (r: Resource, a: Action) => u.permissions?.[r]?.[a] === true
  const ok = qualquer ? qualquer.some((q) => tem(q.resource, q.action)) : tem(meta!.resource, meta!.action)
  if (!ok) return navigateTo('/403')
})
