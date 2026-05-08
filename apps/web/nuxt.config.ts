export default defineNuxtConfig({
  compatibilityDate: '2026-05-05',
  devtools: { enabled: false },
  modules: [
    '@pinia/nuxt',
    '@vueuse/nuxt',
    '@nuxtjs/tailwindcss',
    '@nuxtjs/turnstile',
  ],
  css: ['~/assets/css/globals.css'],
  components: [
    { path: '~/components', pathPrefix: false, extensions: ['vue'] },
  ],
  typescript: {
    strict: true,
    typeCheck: false,
  },
  runtimeConfig: {
    // Server-only (SSR, middleware fetches). In prod set this to
    // http://api:8000 so SSR talks to the api container directly,
    // bypassing Traefik. In dev defaults to public apiUrl.
    apiUrlInternal: process.env.API_URL_INTERNAL || 'http://127.0.0.1:8000',
    turnstile: {
      secretKey: process.env.TURNSTILE_SECRET_KEY || '',
    },
    public: {
      // Client-visible. In prod set absolute origin (https://app.hadken.com).
      // In dev leave empty so $fetch hits same-origin and the routeRules proxy
      // forwards /api/** to the FastAPI backend (no CORS, no PNA).
      apiUrl: process.env.API_URL_PUBLIC ?? '',
      turnstile: {
        siteKey: process.env.TURNSTILE_SITE_KEY || '',
      },
    },
  },
  turnstile: {
    siteKey: process.env.TURNSTILE_SITE_KEY || '',
  },
  nitro: {
    preset: 'node-server',
    devProxy: {
      '/api': {
        target: (process.env.API_URL_INTERNAL || 'http://127.0.0.1:8000') + '/api',
        changeOrigin: true,
      },
    },
  },
  routeRules: {
    '/api/**': { proxy: (process.env.API_URL_INTERNAL || 'http://127.0.0.1:8000') + '/api/**' },
  },
  app: {
    head: {
      title: 'DaVinci',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      ],
      htmlAttrs: { class: '' },
    },
  },
  tailwindcss: {
    cssPath: '~/assets/css/globals.css',
  },
})
