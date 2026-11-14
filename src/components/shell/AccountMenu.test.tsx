import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { AccountMenu } from './AccountMenu';

const logout = vi.fn().mockResolvedValue(undefined);
vi.mock('../../auth/api', () => ({ logout: () => logout() }));

describe('AccountMenu placement', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('en');
  });
  afterEach(() => vi.restoreAllMocks());

  const open = () => {
    render(
      <MemoryRouter>
        <AccountMenu profile={null} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /account menu/i }));
    return screen.getByRole('dialog', { name: /account menu/i });
  };

  it('opens downward when it fits in the viewport', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ bottom: 300 } as DOMRect);
    expect(open().className).toContain('top-full');
  });

  it('opens upward when a downward panel would be clipped by the viewport', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ bottom: window.innerHeight + 200 } as DOMRect);
    const panel = open();
    expect(panel.className).toContain('bottom-full');
    expect(panel.className).not.toContain('top-full');
  });
});

describe('AccountMenu sign out (foroba#278)', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('en');
  });

  it('ends the session server-side then sends the user to /login', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    render(
      <MemoryRouter>
        <AccountMenu profile={null} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /account menu/i }));
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/login'));
    expect(logout).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
});
