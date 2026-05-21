import { defineStore } from 'pinia'

export type AuthUser = {
  id: string
  open_id: string
  email: string
  name: string | null
  role: 'admin' | 'user'
  status: 'pending' | 'active' | 'suspended'
  permissions: Record<string, { view?: boolean; edit?: boolean; delete?: boolean }>
  // Operator-of-stock tag (ci|pi|ra|sa|sp). When non-null and role !== 'admin',
  // the user is locked to /controle-estoque by auth.global middleware.
  stock_tag?: string | null
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as AuthUser | null,
    fetched: false,
  }),

  getters: {
    isAuthenticated: (s) => s.user !== null,
    isAdmin: (s) => s.user?.role === 'admin',
    isActive: (s) => s.user?.status === 'active',
  },

  actions: {
    async fetchMe() {
      const config = useRuntimeConfig()
      // SSR: hit internal docker DNS (no Traefik hop). Client: hit same-origin.
      const base = (import.meta.server
        ? (config as any).apiUrlInternal
        : config.public.apiUrl) as string
      try {
        const r = await $fetch<AuthUser | null>(`${base}/api/auth/me`, {
          credentials: 'include',
          // SSR must propagate the user's cookies to the api
          headers: import.meta.server ? useRequestHeaders(['cookie']) : undefined,
        })
        this.user = r
      } catch {
        this.user = null
      }
      this.fetched = true
    },

    async requestOtp(email: string, turnstileToken?: string) {
      const config = useRuntimeConfig()
      return $fetch<{ prefix: string; expires_at: string }>(
        `${config.public.apiUrl}/api/auth/request`,
        {
          method: 'POST',
          credentials: 'include',
          body: { email, turnstile_token: turnstileToken },
        },
      )
    },

    async verifyOtp(email: string, code: string) {
      const config = useRuntimeConfig()
      const r = await $fetch<{
        user: AuthUser
        requires_approval: boolean
      }>(`${config.public.apiUrl}/api/auth/verify`, {
        method: 'POST',
        credentials: 'include',
        body: { email, code },
      })
      this.user = r.user
      return r
    },

    async resendOtp(email: string) {
      const config = useRuntimeConfig()
      return $fetch<{ prefix: string; expires_at: string }>(
        `${config.public.apiUrl}/api/auth/resend`,
        {
          method: 'POST',
          credentials: 'include',
          body: { email },
        },
      )
    },

    async logout() {
      const config = useRuntimeConfig()
      await $fetch(`${config.public.apiUrl}/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
      this.user = null
    },
  },
})
