import { test, expect } from '@playwright/test'

test.describe('Login (email-OTP)', () => {
  test('email step renders + accepts a typed address', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByRole('heading', { name: /entrar|login/i })).toBeVisible()
    const emailInput = page.getByPlaceholder(/email|e-mail/i).first()
    await emailInput.fill('e2e-test@example.com')
    await expect(emailInput).toHaveValue('e2e-test@example.com')
  })

  test('redirects to /login when not authenticated', async ({ page }) => {
    await page.goto('/produtos')
    await expect(page).toHaveURL(/\/login/)
  })
})
