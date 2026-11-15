import { expect, test } from '@playwright/test';
import { contextFor, enrolTotpViaUi, newIdentity, registerViaUi, waitForMail } from '../support/helpers';

test('household invite: consent-gated join, shared membership, leave', async ({ browser }) => {
  const a = newIdentity('hha');
  const b = newIdentity('hhb');
  const pa = await (await contextFor(browser, a)).newPage();
  const pb = await (await contextFor(browser, b)).newPage();
  await registerViaUi(pa, a);
  await enrolTotpViaUi(pa);
  await registerViaUi(pb, b);
  await enrolTotpViaUi(pb);

  // A invites B; nothing merges until B accepts.
  await pa.goto('/household');
  await pa.getByLabel('Invite by email').fill(b.email);
  await pa.getByRole('button', { name: 'Send invitation' }).click();
  await expect(pa.getByRole('status')).toContainText('Invitation sent');
  // The inviter's household exists, but the invitee is not a member until they accept.
  await pa.reload();
  await expect(pa.getByText(a.email)).toBeVisible();
  await expect(pa.getByText(b.email)).toHaveCount(0);

  const invite = await waitForMail(b.email, /invited/i);
  const token = invite.body.match(/\b([0-9A-F]{12})\b/)?.[1];
  expect(token, 'invite mail carries a 12-hex token').toBeTruthy();

  // A bad token is refused; the real one joins.
  await pb.goto('/household');
  await expect(pb.getByText('No household yet')).toBeVisible();
  await pb.getByLabel('Accept an invite').fill('0'.repeat(12));
  await pb.getByRole('button', { name: 'Accept invite' }).click();
  await expect(pb.getByRole('alert')).toContainText("That code didn't work");
  await pb.getByLabel('Accept an invite').fill(token!);
  await pb.getByRole('button', { name: 'Accept invite' }).click();
  await expect(pb.getByRole('heading', { name: 'Members' })).toBeVisible();
  await expect(pb.getByText(a.email)).toBeVisible();

  await pa.reload();
  await expect(pa.getByText(b.email)).toBeVisible();

  // Sharing is opt-in: every category starts off.
  for (const sw of await pa.getByRole('switch').all()) await expect(sw).not.toBeChecked();

  // B leaves; A is alone again.
  await pb.getByRole('button', { name: 'Leave household' }).click();
  await expect(pb.getByText('No household yet')).toBeVisible();
  await pa.reload();
  await expect(pa.getByText(b.email)).toHaveCount(0);
});
