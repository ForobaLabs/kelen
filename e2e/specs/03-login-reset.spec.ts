import { expect, test, type Page } from '@playwright/test';
import { codeFrom, contextFor, enrolTotpViaUi, newIdentity, registerViaUi, totp, waitForMail, type Identity } from '../support/helpers';

async function signOut(page: Page) {
  if (!/\/profile/.test(page.url())) await page.goto('/profile');
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/login/);
}

async function passwordStep(page: Page, id: Identity, password = id.password) {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(id.email);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test.describe('login, step-up, logout', () => {
  test('wrong password is refused; correct password + TOTP signs in; sign-out ends the session', async ({ browser }) => {
    const id = newIdentity('login');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    const { secret } = await enrolTotpViaUi(page);
    await signOut(page);

    await passwordStep(page, id, id.password + 'x');
    await expect(page.getByRole('alert')).toContainText('Invalid email or password');

    await passwordStep(page, id);
    await expect(page.getByRole('heading', { name: "Verify it's you" })).toBeVisible();
    // The enrolment code is spent for this 30 s window; use the next window's code (server tolerates ±1 step).
    const input = page.locator('input[autocomplete="one-time-code"]');
    await input.fill('000000');
    await page.getByRole('button', { name: 'Verify', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Invalid code');
    await input.fill(totp(secret, Date.now() + 30_000));
    await page.getByRole('button', { name: 'Verify', exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);

    // Session is live: protected route renders; after sign-out it bounces to /login.
    await page.goto('/profile');
    await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible();
    await signOut(page);
    await page.goto('/profile');
    await expect(page).toHaveURL(/\/login/);
  });

  test('a recovery code signs in once and is then burned', async ({ browser }) => {
    const id = newIdentity('recov');
    const ctx = await contextFor(browser, id);
    const page = await ctx.newPage();
    await registerViaUi(page, id);
    const { recoveryCodes } = await enrolTotpViaUi(page);
    await signOut(page);

    for (const attempt of [1, 2]) {
      await passwordStep(page, id);
      await page.getByRole('button', { name: 'Use a recovery code instead' }).click();
      await page.getByLabel('Recovery code').fill(recoveryCodes[0]);
      await page.getByRole('button', { name: 'Verify', exact: true }).click();
      if (attempt === 1) {
        await expect(page).not.toHaveURL(/\/login/);
        await signOut(page);
      } else {
        await expect(page.getByRole('alert')).toContainText('Invalid recovery code');
      }
    }
  });
});

test.describe('password reset', () => {
  test('mailed code resets the password; old password stops working', async ({ browser }) => {
    const id = newIdentity('reset');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    await enrolTotpViaUi(page);
    await signOut(page);

    await page.goto('/login');
    await page.getByRole('button', { name: 'Forgot password?' }).click();
    await page.getByPlaceholder('your@email.com').fill(id.email);
    await page.getByRole('button', { name: 'Send reset code' }).click();
    await expect(page.getByText('Check your email for a 6-digit code')).toBeVisible();
    const mail = await waitForMail(id.email, /./, 2) // 1 = signup code, 2 = reset code;
    const code = codeFrom(mail);

    const newPw = 'Another-Strong-Pass-7!';
    await page.getByPlaceholder('6-digit code').fill(code);
    await page.getByPlaceholder('New password', { exact: true }).fill(newPw);
    await page.getByPlaceholder('Confirm new password').fill(newPw);
    await page.getByRole('button', { name: 'Reset password' }).click();
    await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeHidden();

    await passwordStep(page, id, id.password);
    await expect(page.getByRole('alert')).toContainText('Invalid email or password');
    await passwordStep(page, id, newPw);
    await expect(page.getByRole('heading', { name: "Verify it's you" })).toBeVisible();
  });

  test('a wrong reset code is refused and the password is unchanged', async ({ browser }) => {
    const id = newIdentity('resetbad');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    await enrolTotpViaUi(page);
    await signOut(page);
    await page.goto('/login');
    await page.getByRole('button', { name: 'Forgot password?' }).click();
    await page.getByPlaceholder('your@email.com').fill(id.email);
    await page.getByRole('button', { name: 'Send reset code' }).click();
    await page.getByPlaceholder('6-digit code').fill('000000');
    await page.getByPlaceholder('New password', { exact: true }).fill('Whatever-Strong-1!');
    await page.getByPlaceholder('Confirm new password').fill('Whatever-Strong-1!');
    await page.getByRole('button', { name: 'Reset password' }).click();
    await expect(page.getByRole('alert')).toContainText(/invalid|expired/i);
  });
});
