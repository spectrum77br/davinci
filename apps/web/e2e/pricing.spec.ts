import { test, expect } from './fixtures/auth'

test.describe('Pricing push', () => {
  test('pricing accounts tab renders', async ({ page }) => {
    await page.goto('/pricing/contas')
    await expect(page).toHaveURL(/\/pricing\/contas/)
    await expect(
      page.getByRole('heading', { name: /pre[çc]os|tabela/i })
    ).toBeVisible()
  })
})
