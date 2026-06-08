import { defineStore } from 'pinia'

export type AuthUser = {
  id: string
  open_id: string
  email: string
  name: string | null
  role: 'admin' | 'user'
  status: 'pending' | 'active' | 'suspended'
  permissions: Record<string, { view?: boolean; edit?: boolean; delete?: boolean }>
  // Operator-of-stock tags (multi). When non-empty and role !== 'admin',
  // the user is locked to /controle-estoque by auth.global middleware
  // and sees the union of products matching ANY of the tags.
  // Slugs: ci|pi|ra|sa|sp|us|cd|fake|mala|eletro|insumos.
  stock_tags?: string[] | null
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
      // SSR: hit internal docker DNS (no proxy hop). Client: relative
      // URL — same-origin via Caddy on whichever host served the page.
      const base = (import.meta.server
        ? (config as any).apiUrlInternal
        : '') as string
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

    async login(email: string, password: string) {
      const r = await $fetch<{
        user: AuthUser
        requires_approval: boolean
      }>(`/api/auth/login`, {
        method: 'POST',
        credentials: 'include',
        body: { email, password },
      })
      this.user = r.user
      return r
    },

    async requestOtp(email: string, turnstileToken?: string) {
      return $fetch<{ prefix: string; expires_at: string }>(
        `/api/auth/request`,
        {
          method: 'POST',
          credentials: 'include',
          body: { email, turnstile_token: turnstileToken },
        },
      )
    },

    async verifyOtp(email: string, code: string) {
      const r = await $fetch<{
        user: AuthUser
        requires_approval: boolean
      }>(`/api/auth/verify`, {
        method: 'POST',
        credentials: 'include',
        body: { email, code },
      })
      this.user = r.user
      return r
    },

    async resendOtp(email: string) {
      return $fetch<{ prefix: string; expires_at: string }>(
        `/api/auth/resend`,
        {
          method: 'POST',
          credentials: 'include',
          body: { email },
        },
      )
    },

    async logout() {
      await $fetch(`/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
      this.user = null
    },
  },
})
