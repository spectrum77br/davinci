import { test as base, expect } from '@playwright/test'

export type AuthFixtures = {
  authed: void
}

/**
 * Inject a session cookie before navigation. Skips the test when
 * `E2E_SESSION_COOKIE` is not provided so CI can opt-in selectively.
 */
export const test = base.extend<AuthFixtures>({
  authed: [async ({ context, baseURL }, use, testInfo) => {
    const cookie = process.env.E2E_SESSION_COOKIE
    if (!cookie) {
      testInfo.skip(true, 'E2E_SESSION_COOKIE not set')
    }
    if (cookie && baseURL) {
      const url = new URL(baseURL)
      await context.addCookies([
        {
          name: 'davinci_session',
          value: cookie,
          domain: url.hostname,
          path: '/',
          httpOnly: true,
          sameSite: 'Lax',
          secure: url.protocol === 'https:',
        },
      ])
    }
    await use()
  }, { auto: true }],
})

export { expect }
