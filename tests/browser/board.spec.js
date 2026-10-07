import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => { await page.goto('/'); });

test('board shows its real state and fits the viewport', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Good work. Moving forward.' })).toBeVisible();
  await expect(page.getByRole('article')).toHaveCount(6);
  await expect(page.locator('#completion-count')).toHaveText('2 of 6 tasks');
  await expect(page.locator('#progress-percent')).toHaveText('33%');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('status menu moves a card between active columns and persists it', async ({ page }) => {
  await page.getByLabel('Status for Polish the welcome screen').selectOption('doing');
  await expect(page.locator('[data-column="doing"] [data-task-id="welcome"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-column="doing"] [data-task-id="welcome"]')).toBeVisible();
  await expect(page.locator('#announcement')).toBeAttached();
});

test('search filters cards without changing progress', async ({ page }) => {
  await page.getByRole('searchbox', { name: 'Search tasks' }).fill('onboarding');
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Build the onboarding flow' })).toBeVisible();
  await expect(page.locator('#completion-count')).toHaveText('2 of 6 tasks');
  await page.getByRole('searchbox').fill('not a task');
  await expect(page.getByRole('article')).toHaveCount(0);
  await expect(page.locator('#search-result')).toHaveText('0 tasks match “not a task”');
});

test('reset restores the original board after edits', async ({ page }) => {
  await page.getByLabel('Status for Polish the welcome screen').selectOption('doing');
  await page.getByRole('searchbox').fill('welcome');
  await page.getByRole('button', { name: 'Reset demo board' }).click();
  await expect(page.getByRole('article')).toHaveCount(6);
  await expect(page.locator('[data-column="todo"] [data-task-id="welcome"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-column="todo"] [data-task-id="welcome"]')).toBeVisible();
});

test('drag and drop moves between active columns', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'Touch devices use the accessible status menu.');
  await page.locator('[data-task-id="welcome"]').dragTo(page.locator('[data-column="doing"]'), { targetPosition: { x: 30, y: 45 } });
  await expect(page.locator('[data-column="doing"] [data-task-id="welcome"]')).toBeVisible();
});

test('keyboard shortcut focuses search and Escape clears it', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'Desktop keyboard shortcut.');
  await page.keyboard.press('/');
  await expect(page.getByRole('searchbox')).toBeFocused();
  await page.keyboard.type('welcome');
  await expect(page.getByRole('article')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('article')).toHaveCount(6);
});
