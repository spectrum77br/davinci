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
    turnstile: {
      secretKey: process.env.TURNSTILE_SECRET_KEY || '',
    },
    public: {
      apiUrl: process.env.API_URL || 'http://localhost:8000',
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
