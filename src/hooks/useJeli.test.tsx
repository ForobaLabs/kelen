import '../i18n';
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import { useJeli } from './useJeli';

afterEach(() => vi.unstubAllGlobals());

describe('useJeli daily limit', () => {
  it('shows the friendly daily-limit message on HTTP 429 daily_limit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { code: 'daily_limit' } }), {
          status: 429,
          headers: { 'Content-Type': 'application/json', 'X-AI-Notice': 'daily_limit' },
        }),
      ),
    );
    const { result } = renderHook(() => useJeli('Ada'));
    await act(async () => {
      await result.current.sendMessage('hello');
    });
    const last = result.current.messages.at(-1)!;
    expect(last.content).toBe(i18n.t('jeli.dailyLimit'));
    expect(i18n.t('jeli.dailyLimit')).not.toBe('jeli.dailyLimit');
    expect(result.current.error).toBe(i18n.t('jeli.dailyLimit'));
  });
});
