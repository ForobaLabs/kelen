import { createHmac } from 'node:crypto';
import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test';

export const SINK = 'http://127.0.0.1:2580';
export const PASSWORD = 'Correct-Horse-Battery-9!';

let counter = 0;
const run = Date.now().toString(36);

/** Unique identity per test: email + a distinct client IP so per-IP rate limits never bleed across tests. */
export function newIdentity(label = 'user') {
  counter += 1;
  return {
    name: `E2E ${label} ${counter}`,
    email: `${label}-${run}-${counter}@kelen-e2e.com`,
    password: PASSWORD,
    ip: `10.77.${Math.floor(counter / 250)}.${(counter % 250) + 1}`,
  };
}

export type Identity = ReturnType<typeof newIdentity>;

/** A browser context whose requests carry a unique X-Forwarded-For (uvicorn --proxy-headers trusts localhost). */
export async function contextFor(browser: Browser, id: Identity, locale = 'en-GB'): Promise<BrowserContext> {
  return browser.newContext({ extraHTTPHeaders: { 'X-Forwarded-For': id.ip }, locale });
}

export interface Mail {
  to: string;
  from: string;
  subject: string;
  body: string;
  ts: number;
}

export async function mailsFor(email: string): Promise<Mail[]> {
  const r = await fetch(`${SINK}/messages?to=${encodeURIComponent(email)}`);
  return (await r.json()) as Mail[];
}

/** Wait for the n-th (1-based) mail to `email` whose subject/body matches `match` and return it. */
export async function waitForMail(email: string, match: RegExp = /./, nth = 1): Promise<Mail> {
  let found: Mail | undefined;
  await expect
    .poll(
      async () => {
        const hits = (await mailsFor(email)).filter((m) => match.test(m.subject) || match.test(m.body));
        found = hits[nth - 1];
        return Boolean(found);
      },
      { timeout: 15_000, message: `mail to ${email} matching ${match}` },
    )
    .toBe(true);
  return found as Mail;
}

export function codeFrom(mail: Mail): string {
  const m = mail.body.match(/\b(\d{6})\b/);
  if (!m) throw new Error(`no 6-digit code in mail "${mail.subject}": ${mail.body.slice(0, 200)}`);
  return m[1];
}

export async function registerViaUi(page: Page, id: Identity) {
  await page.goto('/register');
  await page.getByLabel('Full name').fill(id.name);
  await page.getByLabel('Email', { exact: true }).fill(id.email);
  await page.getByLabel('Confirm email').fill(id.email);
  await page.locator('#password').fill(id.password);
  await page.locator('#password-confirm').fill(id.password);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Create account' }).click();
}

// ── TOTP (RFC 6238, SHA-1, 30 s, 6 digits) — computed in-test so no authenticator app is needed ──

function base32Decode(s: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of s.replace(/=+$/, '').replace(/\s/g, '').toUpperCase()) {
    bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export function totp(secret: string, atMs = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(atMs / 30_000)));
  const h = createHmac('sha1', base32Decode(secret)).update(counter).digest();
  const o = h[h.length - 1] & 0xf;
  const n = ((h[o] & 0x7f) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
  return (n % 1_000_000).toString().padStart(6, '0');
}

/** Drive the enrolment wizard through TOTP → recovery codes → onboarding. Returns what the user would save. */
export async function enrolTotpViaUi(page: Page): Promise<{ secret: string; recoveryCodes: string[] }> {
  await expect(page).toHaveURL(/\/enroll/);
  await page.getByRole('button', { name: /Authenticator app/ }).click();
  const secretEl = page.locator('p.break-all');
  await expect(secretEl).toHaveText(/^[A-Z2-7]{16,}$/);
  const secret = ((await secretEl.textContent()) ?? '').trim();
  await page.getByLabel('6-digit code').fill(totp(secret));
  await page.getByRole('button', { name: 'Verify and continue' }).click();
  await expect(page.getByRole('heading', { name: 'Add another way to sign in?' })).toBeVisible();
  await page.getByRole('button', { name: 'Finish setup' }).click();
  await expect(page.getByRole('heading', { name: 'Save your recovery codes' })).toBeVisible();
  const recoveryCodes = (await page.locator('div.font-mono span').allTextContents()).map((c) => c.trim());
  await page.getByRole('button', { name: "I've saved these codes" }).click();
  await expect(page).toHaveURL(/\/onboarding/);
  return { secret, recoveryCodes };
}
