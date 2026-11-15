import { expect, test } from '@playwright/test';
import { SINK, contextFor, enrolTotpViaUi, newIdentity, registerViaUi } from '../support/helpers';

test('account deletion needs the password and the typed phrase, then removes the account', async ({ browser }) => {
  const id = newIdentity('del');
  const page = await (await contextFor(browser, id)).newPage();
  await registerViaUi(page, id);
  await enrolTotpViaUi(page);

  await page.goto('/settings');
  await page.getByRole('button', { name: 'Delete account' }).click();
  const dialog = page.getByRole('dialog');
  const confirm = dialog.getByRole('button', { name: 'Delete account' });
  await expect(confirm).toBeDisabled();

  await dialog.locator('input[type="password"]').fill('Not-The-Password-1!');
  await dialog.getByLabel('Type DELETE to confirm').fill('DELETE');
  await confirm.click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(page).toHaveURL(/\/settings/);

  await dialog.locator('input[type="password"]').fill(id.password);
  await confirm.click();
  await expect(page).toHaveURL(/\/login/);
  const erased = (await (await fetch(`${SINK}/erasures`)).json()) as string[];
  expect(erased).toEqual(expect.arrayContaining(['pazuru', 'tsumi']));

  await page.getByLabel('Email', { exact: true }).fill(id.email);
  await page.locator('#password').fill(id.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid email or password');
});
