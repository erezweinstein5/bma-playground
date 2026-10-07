import { test, expect } from '@playwright/test';

// This is the independent acceptance contract. It fails on the broken demo baseline.
// The repair must change the application; this test stays unchanged.
test.beforeEach(async ({ page }) => { await page.goto('/'); });

test('moving a task to Done updates the board, progress, and persisted state', async ({ page }) => {
  await page.getByLabel('Status for Build the onboarding flow').selectOption('done');
  await expect(page.locator('[data-column="done"] [data-task-id="onboarding"]')).toBeVisible();
  await expect(page.locator('#completion-count')).toHaveText('3 of 6 tasks');
  await expect(page.locator('#progress-percent')).toHaveText('50%');
  await page.reload();
  await expect(page.locator('[data-column="done"] [data-task-id="onboarding"]')).toBeVisible();
  await expect(page.locator('#progress-percent')).toHaveText('50%');
});

test('dragging to Done completes the same real interaction', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'Mobile completion is verified through the status menu.');
  await page.locator('[data-task-id="onboarding"]').dragTo(page.locator('[data-column="done"]'), { targetPosition: { x: 30, y: 45 } });
  await expect(page.locator('[data-column="done"] [data-task-id="onboarding"]')).toBeVisible();
  await expect(page.locator('#completion-count')).toHaveText('3 of 6 tasks');
});
