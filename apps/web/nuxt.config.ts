export default defineNuxtConfig({
  compatibilityDate: '2026-05-05',
  devtools: { enabled: true },
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
    apiUrlInternal: process.env.API_URL_INTERNAL || process.env.API_URL || 'http://127.0.0.1:8001',
    turnstile: {
      secretKey: process.env.TURNSTILE_SECRET_KEY || '',
    },
    public: {
      // Client-visible. In prod use absolute origin (https://app.hadken.com)
      // so $fetch works from browser. In dev a 127.0.0.1:8001 absolute URL.
      apiUrl: process.env.API_URL || 'http://127.0.0.1:8001',
      turnstile: {
        siteKey: process.env.TURNSTILE_SITE_KEY || '',
      },
    },
  },
  turnstile: {
    siteKey: process.env.TURNSTILE_SITE_KEY || '',
  },
  nitro: { preset: 'node-server' },
  app: {
    head: {
      title: 'DaVinci',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      ],
      htmlAttrs: { class: 'dark' },
    },
  },
  tailwindcss: {
    cssPath: '~/assets/css/globals.css',
  },
})
