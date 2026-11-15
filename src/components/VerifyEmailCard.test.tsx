import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';

let user: Record<string, unknown> | null = null;
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user }) }));

import { VerifyEmailCard } from './VerifyEmailCard';

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <VerifyEmailCard />
    </QueryClientProvider>,
  );
  return { invalidate };
}

function stubFetch(handlers: Record<string, () => Response>) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const key = Object.keys(handlers).find((k) => url.endsWith(k));
      if (!key) throw new Error(`Unexpected fetch: ${url}`);
      return Promise.resolve(handlers[key]());
    }),
  );
  return calls;
}

describe('VerifyEmailCard', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
    user = { email: 'a@b.com', email_verified: false };
  });
  afterEach(() => vi.unstubAllGlobals());

  it('renders nothing for a verified user or a signed-out visitor', () => {
    user = { email: 'a@b.com', email_verified: true };
    mount();
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).toBeNull();
    user = null;
    mount();
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).toBeNull();
  });

  it('submits the 6-digit code and refreshes the session on success', async () => {
    const calls = stubFetch({ '/auth/verify-email': () => new Response(null, { status: 204 }) });
    const { invalidate } = mount();
    expect(screen.getByRole('heading', { name: 'Verify your email' })).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: 'Verify' });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Verification code'), '123456');
    await userEvent.click(submit);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe('/jeli/auth/verify-email');
    expect(JSON.parse(calls[0].body ?? '{}')).toEqual({ code: '123456' });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['session'] }));
  });

  it('shows an alert for a wrong or expired code and keeps the card', async () => {
    stubFetch({
      '/auth/verify-email': () => new Response(JSON.stringify({ detail: 'Invalid or expired code' }), { status: 400 }),
    });
    mount();
    await userEvent.type(screen.getByLabelText('Verification code'), '000000');
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That code is invalid or has expired');
    expect(screen.getByRole('heading', { name: 'Verify your email' })).toBeInTheDocument();
  });

  it('resends the code and confirms it', async () => {
    const calls = stubFetch({ '/auth/resend-verification': () => new Response(null, { status: 204 }) });
    mount();
    await userEvent.click(screen.getByRole('button', { name: 'Resend code' }));
    expect(await screen.findByRole('status')).toHaveTextContent('A new code is on its way');
    expect(calls[0].url).toBe('/jeli/auth/resend-verification');
  });

  it('tells the user when email delivery is unavailable on resend (503)', async () => {
    stubFetch({ '/auth/resend-verification': () => new Response(null, { status: 503 }) });
    mount();
    await userEvent.click(screen.getByRole('button', { name: 'Resend code' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Email delivery is unavailable');
  });
});
