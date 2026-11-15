import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Real-auth stack for the account-lifecycle suite (Plan Task 5.1):
//   mail sink (SMTP STARTTLS + HTTP API) → Kelen backend (no AUTH_DEV_BYPASS, fresh DB, SMTP on)
//   → Vite dev server. Ports are the standard dev ports; stop any dev.sh stack first.
const HOME = process.env.HOME ?? '';
const SINK_PY = join(HOME, '.pfh/venvs/e2e/bin/python');
const dbDir = mkdtempSync(join(tmpdir(), 'kelen-e2e-'));

export const SINK_HTTP = 'http://127.0.0.1:2580';

export default defineConfig({
  testDir: './specs',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5175',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      command: `${SINK_PY} support/mail_sink.py`,
      url: `${SINK_HTTP}/messages`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command:
        '../backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002 --proxy-headers --forwarded-allow-ips=127.0.0.1',
      cwd: '../backend',
      url: 'http://127.0.0.1:8002/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        AUTH_DB_PATH: join(dbDir, 'identity.db'),
        SMTP_ENABLED: 'true',
        SMTP_HOST: '127.0.0.1',
        SMTP_PORT: '2525',
        SMTP_USER: '', // backend/.env may carry real creds; the sink has no AUTH
        SMTP_PASSWORD: '',
        SMTP_FROM: 'Kelen <noreply@kelen-e2e.com>',
        // Account deletion cascades to Naforo/Yiriba; the sink stands in for both and records the calls.
        PAZURU_API_URL: 'http://127.0.0.1:2580/pazuru',
        TSUMI_API_URL: 'http://127.0.0.1:2580/tsumi',
        SEED_MOCK_USERS: 'false',
        AUTH_DEV_BYPASS: 'false',
      },
    },
    {
      command: 'pnpm run dev',
      cwd: '..',
      url: 'http://localhost:5175/login',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
