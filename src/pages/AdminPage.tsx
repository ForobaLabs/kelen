import React, { useEffect, useState } from 'react';
import {
  AdminFlagsPanel,
  AdminUsersTable,
  KpiCard,
  PageContainer,
  PasswordInput,
  SectionHeader,
  StatusCard,
  useAdminSettings,
  useAdminUsers,
} from '@foroba/ui';
import type { HealthStatus } from '@foroba/ui';
import { fetchForobaHealthDetailed } from '../api/foroba';
import type { DetailedHealth } from '../api/foroba';
import { fetchPazuruHealthDetailed } from '../api/pazuru';
import { fetchTsumiHealthDetailed } from '../api/tsumi';
import { useAuth } from '../auth/AuthProvider';

type Tab = 'flags' | 'users' | 'invites' | 'kpis' | 'backup' | 'audit' | 'status';

const TABS: { id: Tab; label: string }[] = [
  { id: 'flags', label: 'Flags' },
  { id: 'users', label: 'Users' },
  { id: 'invites', label: 'Invites' },
  { id: 'kpis', label: 'KPIs' },
  { id: 'backup', label: 'Backup' },
  { id: 'audit', label: 'Audit log' },
  { id: 'status', label: 'Status' },
];

export default function AdminPage(): React.ReactElement {
  const [activeTab, setActiveTab] = useState<Tab>('flags');
  const { settings, loading: flagsLoading, toggle } = useAdminSettings();
  const { users, setAccountDisabled } = useAdminUsers();
  const { user } = useAuth();

  return (
    <PageContainer>
      <SectionHeader
        as="h1"
        title="Admin Panel"
        description="Manage feature flags and user accounts"
        className="mb-6"
      />

      {/* Tab bar */}
      <div className="flex gap-2 mb-6 border-b border-[var(--color-border)]">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
                : 'border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            }`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'flags' && (
        <AdminFlagsPanel
          settings={settings}
          onToggle={toggle}
          loading={flagsLoading}
        />
      )}
      {activeTab === 'users' && (
        <AdminUsersTable
          users={users}
          currentUserId={user?.id}
          onSetAccountDisabled={setAccountDisabled}
        />
      )}
      {activeTab === 'invites' && <AdminInvitesPanel />}
      {activeTab === 'kpis' && <AdminKpisPanel />}
      {activeTab === 'backup' && <AdminBackupPanel />}
      {activeTab === 'audit' && <AdminAuditPanel />}
      {activeTab === 'status' && <AdminStatusPanel />}
    </PageContainer>
  );
}

interface InviteCode {
  id: number;
  code: string;
  note: string | null;
  max_uses: number;
  uses_count: number;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

function AdminInvitesPanel(): React.ReactElement {
  const [codes, setCodes] = useState<InviteCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [maxUses, setMaxUses] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const res = await fetch('/jeli/admin/invite-codes');
    if (res.ok) setCodes(await res.json());
  }

  useEffect(() => {
    fetch('/jeli/admin/invite-codes')
      .then((r) => (r.ok ? r.json() : []))
      .then((data: InviteCode[]) => setCodes(data))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/jeli/admin/invite-codes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: note || undefined, max_uses: maxUses }),
      });
      if (!res.ok) throw new Error(`Failed to create invite code (${res.status})`);
      setNote('');
      setMaxUses(1);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create invite code');
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(id: number) {
    setError(null);
    try {
      const res = await fetch(`/jeli/admin/invite-codes/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Failed to revoke invite code (${res.status})`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke invite code');
    }
  }

  if (loading) return <p>Loading…</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="block text-xs text-[var(--color-text-secondary)] mb-1">Note</label>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. beta batch 1"
            className="px-3 py-2 rounded border border-[var(--color-border)] bg-transparent text-[var(--color-text-primary)]"
          />
        </div>
        <div>
          <label className="block text-xs text-[var(--color-text-secondary)] mb-1">Max uses</label>
          <input
            type="number"
            min={1}
            value={maxUses}
            onChange={(e) => setMaxUses(Number(e.target.value))}
            className="w-24 px-3 py-2 rounded border border-[var(--color-border)] bg-transparent text-[var(--color-text-primary)]"
          />
        </div>
        <button
          onClick={handleCreate}
          disabled={busy}
          className="px-4 py-2 rounded bg-[var(--color-accent)] text-white text-sm font-medium disabled:opacity-50"
        >
          {busy ? 'Creating…' : 'Generate code'}
        </button>
      </div>
      {error && <p role="alert" className="text-sm text-[var(--color-negative)]">{error}</p>}

      {codes.length === 0 ? (
        <p>No invite codes yet.</p>
      ) : (
        <table aria-label="Invite codes" className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-xs uppercase tracking-wide text-[var(--color-text-secondary)]">
              <th className="py-2 pr-4">Code</th>
              <th className="py-2 pr-4">Note</th>
              <th className="py-2 pr-4">Uses</th>
              <th className="py-2 pr-4">Expires</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Action</th>
            </tr>
          </thead>
          <tbody>
            {codes.map((c) => (
              <tr key={c.id} className="border-b border-[var(--color-border)] text-[var(--color-text-primary)]">
                <td className="py-2 pr-4">
                  <code className="rounded bg-black/20 px-1.5 py-0.5">{c.code}</code>
                </td>
                <td className="py-2 pr-4">{c.note ?? '—'}</td>
                <td className="py-2 pr-4 tabular-nums">{c.uses_count}/{c.max_uses}</td>
                <td className="py-2 pr-4">{c.expires_at ?? 'Never'}</td>
                <td className="py-2 pr-4">
                  <span
                    className={
                      c.revoked_at
                        ? 'text-[var(--color-negative)]'
                        : 'text-[var(--color-positive)]'
                    }
                  >
                    {c.revoked_at ? 'Revoked' : 'Active'}
                  </span>
                </td>
                <td className="py-2 pr-4">
                  {!c.revoked_at && (
                    <button
                      type="button"
                      onClick={() => handleRevoke(c.id)}
                      className="text-[var(--color-negative)] underline underline-offset-2"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface AdminKpis {
  total_users: number;
  users_by_tier: Record<string, number>;
  users_by_audience: Record<string, number>;
  signups_last_7d: number;
  signups_last_30d: number;
  mfa_enrolled: number;
  mfa_enrollment_pending: number;
  accounts_disabled: number;
  admins: number;
}

function AdminKpisPanel(): React.ReactElement {
  const [kpis, setKpis] = useState<AdminKpis | null>(null);

  useEffect(() => {
    fetch('/jeli/admin/kpis')
      .then((r) => r.json())
      .then(setKpis);
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KpiCard label="Total users" value={String(kpis?.total_users ?? 0)} loading={!kpis} />
      <KpiCard label="Admins" value={String(kpis?.admins ?? 0)} loading={!kpis} />
      <KpiCard label="Signups (7d)" value={String(kpis?.signups_last_7d ?? 0)} loading={!kpis} />
      <KpiCard label="Signups (30d)" value={String(kpis?.signups_last_30d ?? 0)} loading={!kpis} />
      <KpiCard label="MFA enrolled" value={String(kpis?.mfa_enrolled ?? 0)} loading={!kpis} />
      <KpiCard
        label="MFA pending"
        value={String(kpis?.mfa_enrollment_pending ?? 0)}
        loading={!kpis}
      />
      <KpiCard label="Accounts disabled" value={String(kpis?.accounts_disabled ?? 0)} loading={!kpis} />
      <KpiCard
        label="By tier"
        value={
          kpis
            ? Object.entries(kpis.users_by_tier)
                .map(([k, v]) => `${k}: ${v}`)
                .join(', ')
            : '—'
        }
        loading={!kpis}
      />
    </div>
  );
}

const MIN_PASSPHRASE = 12;

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  try {
    const body = await resp.json();
    if (body && typeof body.detail === 'string') return body.detail;
  } catch {
    /* non-JSON error body */
  }
  return `${fallback} (${resp.status})`;
}

function AdminBackupPanel(): React.ReactElement {
  const [exportPassword, setExportPassword] = useState('');
  const [exportTotp, setExportTotp] = useState('');
  const [exportPassphrase, setExportPassphrase] = useState('');
  const [exportConfirm, setExportConfirm] = useState('');
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [importPassword, setImportPassword] = useState('');
  const [importTotp, setImportTotp] = useState('');
  const [importPassphrase, setImportPassphrase] = useState('');
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null);

  const exportReady =
    !!exportPassword &&
    exportTotp.length >= 6 &&
    exportPassphrase.length >= MIN_PASSPHRASE &&
    exportPassphrase === exportConfirm;

  async function handleExport() {
    setExportBusy(true);
    setExportError(null);
    try {
      const resp = await fetch('/jeli/admin/backup/export-suite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          password: exportPassword,
          totp_code: exportTotp,
          passphrase: exportPassphrase,
        }),
      });
      if (!resp.ok) throw new Error(await errorMessage(resp, 'Export failed'));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pfh-backup-${Date.now()}.pfhbak`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setExportTotp('');
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExportBusy(false);
    }
  }

  async function handleImport() {
    if (!importFile) return;
    const confirmed = window.confirm(
      'This will overwrite live data on all 3 apps (Kelen, Naforo, Yiriba). Continue?'
    );
    if (!confirmed) return;

    setImportBusy(true);
    setImportError(null);
    setImportResult(null);
    try {
      const form = new FormData();
      form.append('file', importFile);
      form.append('password', importPassword);
      form.append('totp_code', importTotp);
      form.append('passphrase', importPassphrase);
      const resp = await fetch('/jeli/admin/backup/import-suite', {
        method: 'POST',
        credentials: 'same-origin',
        body: form,
      });
      if (!resp.ok) throw new Error(await errorMessage(resp, 'Restore failed'));
      setImportResult(await resp.json());
      setImportTotp('');
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Restore failed');
    } finally {
      setImportBusy(false);
    }
  }

  return (
    <div className="space-y-8 max-w-xl">
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)] mb-2">Export backup</h3>
        <p className="text-sm text-[var(--color-text-secondary)] mb-3">
          Downloads one encrypted file containing Kelen, Naforo, and Yiriba data, including the
          signing keys (disaster-recovery copy). Confirm it is you with your password and a fresh
          authenticator code. The passphrase is never stored anywhere — write it down, you'll need
          it to restore. Verify step-up for Naforo and Yiriba first.
        </p>
        <PasswordInput
          value={exportPassword}
          onChange={(e) => setExportPassword(e.target.value)}
          placeholder="Your password"
          autoComplete="current-password"
        />
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          value={exportTotp}
          onChange={(e) => setExportTotp(e.target.value.trim())}
          placeholder="Authenticator code"
          className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-sm text-[var(--color-text-primary)]"
        />
        <PasswordInput
          value={exportPassphrase}
          onChange={(e) => setExportPassphrase(e.target.value)}
          placeholder={`Backup passphrase (${MIN_PASSPHRASE}+ characters)`}
          autoComplete="new-password"
        />
        <PasswordInput
          value={exportConfirm}
          onChange={(e) => setExportConfirm(e.target.value)}
          placeholder="Repeat passphrase"
          autoComplete="new-password"
        />
        <button
          onClick={handleExport}
          disabled={exportBusy || !exportReady}
          className="px-4 py-2 rounded bg-[var(--color-accent)] text-white text-sm font-medium disabled:opacity-50"
        >
          {exportBusy ? 'Exporting…' : 'Export backup'}
        </button>
        {exportError && <p className="text-sm text-[var(--color-negative)] mt-2">{exportError}</p>}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)] mb-2">Restore backup</h3>
        <p className="text-sm text-[var(--color-text-secondary)] mb-3">
          Overwrites live data on all 3 apps. All 3 backends need a manual restart after restoring.
        </p>
        <input
          type="file"
          accept=".pfhbak"
          aria-label="Backup file"
          onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
          className="w-full mb-2 text-sm text-[var(--color-text-primary)]"
        />
        <PasswordInput
          value={importPassword}
          onChange={(e) => setImportPassword(e.target.value)}
          placeholder="Restore: your password"
          autoComplete="current-password"
        />
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          value={importTotp}
          onChange={(e) => setImportTotp(e.target.value.trim())}
          placeholder="Restore: authenticator code"
          className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-sm text-[var(--color-text-primary)]"
        />
        <PasswordInput
          value={importPassphrase}
          onChange={(e) => setImportPassphrase(e.target.value)}
          placeholder="Restore: backup passphrase"
          autoComplete="off"
        />
        <button
          onClick={handleImport}
          disabled={
            importBusy || !importFile || !importPassword || importTotp.length < 6 || !importPassphrase
          }
          className="px-4 py-2 rounded bg-[var(--color-negative)] text-white text-sm font-medium disabled:opacity-50"
        >
          {importBusy ? 'Restoring…' : 'Restore backup'}
        </button>
        {importError && <p className="text-sm text-[var(--color-negative)] mt-2">{importError}</p>}
        {importResult && (
          <pre className="text-xs mt-3 p-3 rounded bg-[var(--color-surface)] border border-[var(--color-border)] overflow-auto">
            {JSON.stringify(importResult, null, 2)}
          </pre>
        )}
      </section>
    </div>
  );
}

interface AuditRow {
  id: number;
  admin_user_id: number;
  action: string;
  target: string | null;
  detail: string | null;
  ip: string | null;
  created_at: string;
}

function AdminAuditPanel(): React.ReactElement {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/jeli/admin/audit-log', { credentials: 'same-origin' })
      .then(async (r) => {
        if (!r.ok) throw new Error(await errorMessage(r, 'Could not load audit log'));
        return r.json();
      })
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load audit log'));
  }, []);

  if (error) return <p className="text-sm text-[var(--color-negative)]">{error}</p>;
  if (!rows) return <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>;
  if (rows.length === 0)
    return <p className="text-sm text-[var(--color-text-secondary)]">No admin actions recorded yet.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead className="text-[var(--color-text-secondary)]">
          <tr>
            <th className="py-2 pr-4">When (UTC)</th>
            <th className="py-2 pr-4">Admin</th>
            <th className="py-2 pr-4">Action</th>
            <th className="py-2 pr-4">Target</th>
            <th className="py-2 pr-4">Detail</th>
            <th className="py-2">IP</th>
          </tr>
        </thead>
        <tbody className="text-[var(--color-text-primary)]">
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[var(--color-border)]">
              <td className="py-2 pr-4 tabular-nums">{r.created_at}</td>
              <td className="py-2 pr-4">{r.admin_user_id}</td>
              <td className="py-2 pr-4">{r.action}</td>
              <td className="py-2 pr-4">{r.target ?? '—'}</td>
              <td className="py-2 pr-4">{r.detail ?? '—'}</td>
              <td className="py-2">{r.ip ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function toHealthStatus(backendStatus: string): HealthStatus {
  if (backendStatus === 'ok') return 'ok';
  if (backendStatus === 'degraded') return 'down';
  return 'unreachable';
}

function AdminStatusPanel(): React.ReactElement {
  const [apps, setApps] = useState<
    { name: string; health: DetailedHealth; lastChecked: string }[]
  >([]);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      const [foroba, pazuru, tsumi] = await Promise.allSettled([
        fetchForobaHealthDetailed(),
        fetchPazuruHealthDetailed(),
        fetchTsumiHealthDetailed(),
      ]);
      if (cancelled) return;

      const unavailable: DetailedHealth = { status: 'unavailable', dependencies: {} };
      setApps([
        { name: 'Kelen', health: foroba.status === 'fulfilled' ? foroba.value : unavailable, lastChecked: new Date().toISOString() },
        { name: 'Naforo', health: pazuru.status === 'fulfilled' ? pazuru.value : unavailable, lastChecked: new Date().toISOString() },
        { name: 'Yiriba', health: tsumi.status === 'fulfilled' ? tsumi.value : unavailable, lastChecked: new Date().toISOString() },
      ]);
    }

    void refresh();
    const id = setInterval(() => void refresh(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {apps.map(({ name, health, lastChecked }) => (
        <StatusCard
          key={name}
          name={name}
          status={toHealthStatus(health.status)}
          lastChecked={lastChecked}
        >
          {Object.entries(health.dependencies).map(([depName, dep]) => (
            <div key={depName} className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-secondary)] capitalize">{depName}</span>
              <span
                className={
                  dep.status === 'ok'
                    ? 'text-[var(--color-positive)]'
                    : dep.status === 'degraded'
                      ? 'text-[var(--color-warning)]'
                      : 'text-[var(--color-text-muted)]'
                }
              >
                {dep.status}
              </span>
            </div>
          ))}
        </StatusCard>
      ))}
    </div>
  );
}
