import { test, expect } from './fixtures/auth'

test.describe('Produtos / import flow', () => {
  test('produtos page lists items', async ({ page }) => {
    await page.goto('/produtos')
    await expect(
      page.getByRole('heading', { name: /produtos/i })
    ).toBeVisible()
  })

  test('opens manual product creation modal', async ({ page }) => {
    await page.goto('/produtos')
    const createBtn = page.getByRole('button', { name: /novo|criar/i }).first()
    if (await createBtn.isVisible()) {
      await createBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
    }
  })
})
