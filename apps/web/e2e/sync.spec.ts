import { test, expect } from './fixtures/auth'

test.describe('Sync flow', () => {
  test('sincronizacoes page renders', async ({ page }) => {
    await page.goto('/sincronizacoes')
    await expect(
      page.getByRole('heading', { name: /sincroniza/i })
    ).toBeVisible()
  })

  test('sync-logs page renders', async ({ page }) => {
    await page.goto('/sync-logs')
    await expect(page).toHaveURL(/\/sync-logs/)
  })
})
