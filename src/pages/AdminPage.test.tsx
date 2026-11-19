import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AdminPage from './AdminPage';

vi.mock('@foroba/ui', async () => {
  const actual = await vi.importActual<typeof import('@foroba/ui')>('@foroba/ui');
  return {
    ...actual,
    useAdminSettings: () => ({ settings: [], loading: false, toggle: vi.fn() }),
    useAdminUsers: () => ({ users: [] }),
  };
});

describe('AdminPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a Status tab that fetches detailed health for all 3 apps', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ok',
        checks: { db: 'ok' },
        dependencies: { ollama: { status: 'ok', last_checked: '2026-07-14T10:00:00Z' } },
      }),
    } as Response);

    render(<AdminPage />);
    fireEvent.click(screen.getByText('Status'));

    await waitFor(() => {
      expect(screen.getAllByText('Operational').length).toBeGreaterThan(0);
    });
  });

  describe('Backup tab (AW-3 re-auth)', () => {
    function openBackup() {
      render(<AdminPage />);
      fireEvent.click(screen.getByText('Backup'));
    }

    function fillExport(over: Partial<Record<string, string>> = {}) {
      const v = {
        pw: 'Admin1234!',
        totp: '123456',
        pass: 'correct horse battery',
        confirm: 'correct horse battery',
        ...over,
      };
      fireEvent.change(screen.getByPlaceholderText('Your password'), { target: { value: v.pw } });
      fireEvent.change(screen.getByPlaceholderText('Authenticator code'), {
        target: { value: v.totp },
      });
      fireEvent.change(screen.getByPlaceholderText('Backup passphrase (12+ characters)'), {
        target: { value: v.pass },
      });
      fireEvent.change(screen.getByPlaceholderText('Repeat passphrase'), {
        target: { value: v.confirm },
      });
    }

    it('keeps Export disabled until password, TOTP and a matching 12+ char passphrase are given', () => {
      openBackup();
      const btn = screen.getByRole('button', { name: 'Export backup' });
      expect(btn).toBeDisabled();
      fillExport({ pass: 'short', confirm: 'short' });
      expect(btn).toBeDisabled();
      fillExport({ confirm: 'different passphrase' });
      expect(btn).toBeDisabled();
      fillExport();
      expect(btn).toBeEnabled();
    });

    it('posts password, totp_code and passphrase to export-suite', async () => {
      const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['x']),
      } as Response);
      globalThis.URL.createObjectURL = vi.fn(() => 'blob:x');
      globalThis.URL.revokeObjectURL = vi.fn();
      openBackup();
      fillExport();
      fireEvent.click(screen.getByRole('button', { name: 'Export backup' }));
      await waitFor(() => expect(spy).toHaveBeenCalled());
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe('/jeli/admin/backup/export-suite');
      expect(JSON.parse((init as RequestInit).body as string)).toEqual({
        password: 'Admin1234!',
        totp_code: '123456',
        passphrase: 'correct horse battery',
      });
    });

    it('shows the server reason when export is refused', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: 'Verify step-up for Naforo and Yiriba first' }),
      } as Response);
      openBackup();
      fillExport();
      fireEvent.click(screen.getByRole('button', { name: 'Export backup' }));
      expect(
        await screen.findByText('Verify step-up for Naforo and Yiriba first'),
      ).toBeInTheDocument();
    });

    it('sends re-auth fields with the restore form', async () => {
      const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
        ok: true,
        json: async () => ({ foroba: {} }),
      } as Response);
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      openBackup();
      const file = new File(['x'], 'b.pfhbak');
      fireEvent.change(screen.getByLabelText('Backup file'), { target: { files: [file] } });
      fireEvent.change(screen.getByPlaceholderText('Restore: your password'), {
        target: { value: 'Admin1234!' },
      });
      fireEvent.change(screen.getByPlaceholderText('Restore: authenticator code'), {
        target: { value: '654321' },
      });
      fireEvent.change(screen.getByPlaceholderText('Restore: backup passphrase'), {
        target: { value: 'correct horse battery' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Restore backup' }));
      await waitFor(() => expect(spy).toHaveBeenCalled());
      const form = (spy.mock.calls[0][1] as RequestInit).body as FormData;
      expect(form.get('password')).toBe('Admin1234!');
      expect(form.get('totp_code')).toBe('654321');
      expect(form.get('passphrase')).toBe('correct horse battery');
    });
  });

  it('renders an Audit log tab listing admin actions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: 1,
          admin_user_id: 3,
          action: 'backup.export',
          target: null,
          detail: null,
          ip: '203.0.113.9',
          created_at: '2026-11-19 10:00:00',
        },
      ],
    } as Response);
    render(<AdminPage />);
    fireEvent.click(screen.getByText('Audit log'));
    expect(await screen.findByText('backup.export')).toBeInTheDocument();
    expect(screen.getByText('203.0.113.9')).toBeInTheDocument();
  });
});
