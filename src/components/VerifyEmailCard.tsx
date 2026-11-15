import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../auth/AuthProvider';
import { EmailDeliveryUnavailableError, resendVerification, verifyEmail } from '../auth/api';

type Feedback = { kind: 'error' | 'info'; text: string } | null;

/** Shown while the account's signup email is unverified: enter the mailed code or ask for a new one. */
export function VerifyEmailCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  if (!user || user.email_verified !== false) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      await verifyEmail(code);
      await qc.invalidateQueries({ queryKey: ['session'] });
    } catch {
      setFeedback({ kind: 'error', text: t('verifyEmail.invalid') });
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setFeedback(null);
    try {
      await resendVerification();
      setFeedback({ kind: 'info', text: t('verifyEmail.resent') });
    } catch (err) {
      const unavailable = err instanceof EmailDeliveryUnavailableError;
      setFeedback({ kind: 'error', text: t(unavailable ? 'verifyEmail.unavailable' : 'verifyEmail.error') });
    }
  }

  return (
    <section className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-6">
      <h2 className="text-base font-semibold">{t('verifyEmail.title')}</h2>
      <p className="mt-1 text-sm text-[var(--color-text-muted)]">{t('verifyEmail.body')}</p>
      <form onSubmit={submit} className="mt-3 flex flex-wrap gap-3">
        <input
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          aria-label={t('verifyEmail.codeLabel')}
          placeholder="123456"
          className="w-40 rounded-lg border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm tabular-nums"
        />
        <button
          type="submit"
          disabled={busy || code.length !== 6}
          className="min-h-11 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
        >
          {busy ? t('verifyEmail.verifying') : t('verifyEmail.verify')}
        </button>
        <button
          type="button"
          onClick={resend}
          className="min-h-11 rounded-lg px-3 py-2 text-sm text-[var(--color-accent)]"
        >
          {t('verifyEmail.resend')}
        </button>
      </form>
      {feedback && (
        <p role={feedback.kind === 'error' ? 'alert' : 'status'} className="mt-2 text-sm">
          {feedback.text}
        </p>
      )}
    </section>
  );
}
