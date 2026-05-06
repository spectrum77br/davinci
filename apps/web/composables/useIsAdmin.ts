import { computed } from 'vue'

export function useIsAdmin() {
  const auth = useAuthStore()
  return computed(() => auth.user?.role === 'admin')
}
