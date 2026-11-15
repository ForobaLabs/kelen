import { expect, test } from '@playwright/test';
import { codeFrom, contextFor, enrolTotpViaUi, mailsFor, newIdentity, registerViaUi, totp, waitForMail } from '../support/helpers';

test.describe('email verification', () => {
  test('profile card: wrong code is rejected, the resent code verifies, the old one is void', async ({ browser }) => {
    const id = newIdentity('verify');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    const signupCode = codeFrom(await waitForMail(id.email));
    await enrolTotpViaUi(page);

    await page.goto('/profile');
    const card = page.getByRole('heading', { name: 'Verify your email' });
    await expect(card).toBeVisible();

    await page.getByLabel('Verification code').fill('000000');
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.getByRole('alert')).toContainText('invalid or has expired');
    await expect(card).toBeVisible();

    await page.getByRole('button', { name: 'Resend code' }).click();
    await expect(page.getByRole('status')).toContainText('new code is on its way');
    const second = codeFrom((await waitForMail(id.email, /./, 2)));

    // The first code was voided by the resend (single outstanding code).
    if (second !== signupCode) {
      await page.getByLabel('Verification code').fill(signupCode);
      await page.getByRole('button', { name: 'Verify' }).click();
      await expect(page.getByRole('alert')).toContainText('invalid or has expired');
    }

    await page.getByLabel('Verification code').fill(second);
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(card).toBeHidden();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible();
    await expect(card).toBeHidden();
  });
});

test.describe('MFA enrolment', () => {
  test('TOTP: sign-up → authenticator code → recovery codes shown once → onboarding', async ({ browser }) => {
    const id = newIdentity('totp');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    const { recoveryCodes } = await enrolTotpViaUi(page);
    expect(recoveryCodes.length).toBeGreaterThanOrEqual(8);
    expect(new Set(recoveryCodes).size).toBe(recoveryCodes.length);
    // Recovery codes are not retrievable afterwards: the wizard is gone and /enroll bounces an enrolled user.
    await page.goto('/enroll');
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByText(recoveryCodes[0])).toHaveCount(0);
  });

  test('TOTP: a wrong authenticator code is refused', async ({ browser }) => {
    const id = newIdentity('totpbad');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    await expect(page).toHaveURL(/\/enroll/);
    await page.getByRole('button', { name: /Authenticator app/ }).click();
    const secretEl = page.locator('p.break-all');
    await expect(secretEl).toHaveText(/^[A-Z2-7]{16,}$/);
    const secret = ((await secretEl.textContent()) ?? '').trim();
    const good = totp(secret);
    const bad = good === '000000' ? '111111' : '000000';
    await page.getByLabel('6-digit code').fill(bad);
    await page.getByRole('button', { name: 'Verify and continue' }).click();
    await expect(page.getByRole('alert')).toContainText('Invalid code');
    await page.getByLabel('6-digit code').fill(totp(secret));
    await page.getByRole('button', { name: 'Verify and continue' }).click();
    await expect(page.getByRole('heading', { name: 'Add another way to sign in?' })).toBeVisible();
  });

  test('email code: mailed 6-digit code enrols the method', async ({ browser }) => {
    const id = newIdentity('emailotp');
    const page = await (await contextFor(browser, id)).newPage();
    await registerViaUi(page, id);
    await waitForMail(id.email); // signup code
    await page.getByRole('button', { name: /Email code/ }).click();
    await expect(page.getByText('We sent a 6-digit code to your email.')).toBeVisible();
    const mails = await mailsFor(id.email);
    expect(mails.length).toBeGreaterThanOrEqual(2);
    const code = codeFrom(await waitForMail(id.email, /./, 2));
    await page.getByLabel('6-digit code').fill(code);
    await page.getByRole('button', { name: 'Verify and continue' }).click();
    await expect(page.getByRole('heading', { name: 'Add another way to sign in?' })).toBeVisible();
  });
});
