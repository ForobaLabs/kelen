import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { contextFor, enrolTotpViaUi, newIdentity, registerViaUi, type Identity } from '../support/helpers';

// AW-6 Step 5: passkeys end to end with a CDP virtual authenticator (no hardware, no prompts).
async function addVirtualAuthenticator(ctx: BrowserContext, page: Page) {
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return cdp;
}

async function signOut(page: Page) {
  if (!/\/profile/.test(page.url())) await page.goto('/profile');
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/login/);
}

async function passwordStep(page: Page, id: Identity) {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(id.email);
  await page.locator('#password').fill(id.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test.describe('passkeys (virtual authenticator)', () => {
  test('register a passkey, sign in with it, and use it for step-up', async ({ browser }) => {
    const id = newIdentity('passkey');
    const ctx = await contextFor(browser, id);
    const page = await ctx.newPage();
    await addVirtualAuthenticator(ctx, page);

    await registerViaUi(page, id);
    await enrolTotpViaUi(page);

    // 1. register the passkey from the security section of the settings page
    await page.goto('/settings');
    await page.getByRole('button', { name: 'Add Passkey' }).click();
    // the button also vanishes while registration is in flight: the real signal is the "Not set up" row going away
    await expect(page.getByText('Passkey — Not set up')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByRole('alert')).toHaveCount(0);

    // 2. log out, then log in with the passkey alone (no password, no TOTP)
    await signOut(page);
    await page.getByLabel('Email', { exact: true }).fill(id.email);
    await page.getByRole('button', { name: 'Sign in with passkey', exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto('/profile');
    await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible();

    // 3. password first, then satisfy the second factor with the passkey instead of TOTP
    await signOut(page);
    await passwordStep(page, id);
    await expect(page.getByRole('heading', { name: "Verify it's you" })).toBeVisible();
    await page.getByRole('button', { name: 'Sign in with passkey instead' }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto('/profile');
    await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible();
  });
});
