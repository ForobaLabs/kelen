import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';

vi.mock('./BillingSection', () => ({ BillingSection: () => null }));
vi.mock('../components/VerifyEmailCard', () => ({ VerifyEmailCard: () => null }));
vi.mock('@foroba/ui', () => ({
  ProfileEditor: () => null,
  FinanceLevelBadge: () => null,
  PageContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SectionHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
  useUserProfile: () => ({ profile: { id: 'x', financeLevel: 'base' }, updateProfile: vi.fn() }),
  useSuiteProfile: () => ({
    profile: { firstName: 'A', lastName: 'B', fullName: 'A B', avatarUrl: null },
    refetch: vi.fn(),
  }),
}));

import { ProfilePage } from './ProfilePage';

beforeAll(() => i18n.changeLanguage('en'));
afterEach(() => vi.unstubAllGlobals());

async function pick(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, file);
}

describe('ProfilePage avatar upload', () => {
  it('shows a friendly message when the upload is rejected as too large (413)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 413 }))));
    render(<ProfilePage />);
    await pick(new File(['x'], 'big.png', { type: 'image/png' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/too large/i);
  });

  it('shows a generic message on any other failure and none on success', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))));
    const { unmount } = render(<ProfilePage />);
    await pick(new File(['x'], 'a.png', { type: 'image/png' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not upload/i);
    unmount();

    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"avatar_url":"/a.png"}', { status: 200 }))));
    render(<ProfilePage />);
    await pick(new File(['x'], 'a.png', { type: 'image/png' }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
