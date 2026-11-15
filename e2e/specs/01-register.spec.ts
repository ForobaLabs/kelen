import { expect, test } from '@playwright/test';
import { codeFrom, contextFor, newIdentity, registerViaUi, waitForMail } from '../support/helpers';

test.describe('sign-up', () => {
  test('submit stays disabled until every required field and the ToS box are set', async ({ browser }) => {
    const id = newIdentity('req');
    const page = await (await contextFor(browser, id)).newPage();
    await page.goto('/register');
    const submit = page.getByRole('button', { name: 'Create account' });
    await expect(submit).toBeDisabled();
    await page.getByLabel('Full name').fill(id.name);
    await page.getByLabel('Email', { exact: true }).fill(id.email);
    await page.getByLabel('Confirm email').fill(id.email);
    await page.locator('#password').fill(id.password);
    await page.locator('#password-confirm').fill(id.password);
    await expect(submit).toBeDisabled(); // ToS not yet accepted
    await page.getByRole('checkbox').check();
    await expect(submit).toBeEnabled();
    await page.getByRole('checkbox').uncheck();
    await expect(submit).toBeDisabled();
  });

  test('mismatched email and password show inline errors and block submit', async ({ browser }) => {
    const id = newIdentity('mismatch');
    const page = await (await contextFor(browser, id)).newPage();
    await page.goto('/register');
    await page.getByLabel('Full name').fill(id.name);
    await page.getByLabel('Email', { exact: true }).fill(id.email);
    await page.getByLabel('Confirm email').fill('other@kelen-e2e.com');
    await page.locator('#password').fill(id.password);
    await page.locator('#password-confirm').fill(id.password + 'x');
    await page.getByRole('checkbox').check();
    await expect(page.getByText('Emails do not match')).toBeVisible();
    await expect(page.getByText('Passwords do not match')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create account' })).toBeDisabled();
  });

  test('a valid sign-up lands on MFA enrolment and sends a 6-digit verification code', async ({ browser }) => {
    const id = newIdentity('ok');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    await expect(page).toHaveURL(/\/enroll/);
    await expect(page.getByRole('heading', { name: 'Secure your account' })).toBeVisible();
    const mail = await waitForMail(id.email);
    expect(codeFrom(mail)).toMatch(/^\d{6}$/);
  });

  test('registering an existing email is refused with a visible error', async ({ browser }) => {
    const id = newIdentity('dup');
    const first = await (await contextFor(browser, id)).newPage();
    await registerViaUi(first, id);
    await expect(first).toHaveURL(/\/enroll/);
    const other = await (await contextFor(browser, newIdentity('dupip'))).newPage();
    await registerViaUi(other, id);
    await expect(other.getByRole('alert')).toBeVisible();
    await expect(other).toHaveURL(/\/register/);
  });

  for (const [lng, title] of [
    ['it', 'Crea il tuo account'],
    ['fr', 'Créez votre compte'],
  ] as const) {
    test(`register page renders in ${lng}`, async ({ browser }) => {
      const id = newIdentity(`l10n${lng}`);
      const ctx = await contextFor(browser, id);
      await ctx.addInitScript((l) => localStorage.setItem('foroba.language', l), lng);
      const page = await ctx.newPage();
      await page.goto('/register');
      await expect(page.getByRole('heading', { name: title })).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('lang', lng);
    });
  }
});
