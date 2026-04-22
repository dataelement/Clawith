import { expect, test } from '@playwright/test';

test('login page smoke check', async ({ page }) => {
  await page.goto('/login');

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('heading', { name: /clawith/i })).toBeVisible();
  await expect(page.locator('input')).toHaveCount(2);
});
