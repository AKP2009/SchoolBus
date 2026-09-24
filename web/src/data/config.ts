/**
 * Data source switch. Screens never import mocks, supabase or fetch directly: they call the
 * hooks in `web/src/data/hooks.ts`, which read from here. Moving to real data = VITE_USE_MOCKS=false.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== 'false';
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL = import.meta.env.VITE_WS_URL ?? API_URL.replace(/^http/, 'ws');

/**
 * Live mode replays loaded history (Supabase holds 2026-08-16 → 08-29), so "now" is data time:
 * the stream's last telemetry ts, else the running replay's clock, else VITE_DATA_NOW, else the
 * wall clock. It picks the operator's shift at sign-in and bounds "last 7 days" style queries.
 */
const dataNowEnv = import.meta.env.VITE_DATA_NOW ? Date.parse(import.meta.env.VITE_DATA_NOW) : NaN;
export const DATA_NOW: number | null = Number.isFinite(dataNowEnv) ? dataNowEnv : null;

/** Demo panel defaults (docs/demo_script.md "Demo data"). */
export const DEMO_REPLAY = { machineIds: ['M04', 'M05'], from: '2026-08-19T15:15:00Z', speed: 10, machineId: 'M05', operatorId: 'OP02' };

/** Mock responses wait this long so loading states are visible and honest. */
export const MOCK_LATENCY_MS = 250;

export type ForcedState = 'loading' | 'empty' | 'error' | 'offline';

/**
 * QA / screenshot overrides from the URL (mock mode only):
 *   ?state=loading|empty|error|offline   every data hook shows that state
 *   ?t=6700                               mock replay position in data seconds
 *   ?speed=0                              mock replay speed (0 = paused)
 *   ?as=OP02                              sign the cab in as this operator
 */
function params(): URLSearchParams {
  return typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
}

export function forcedState(): ForcedState | null {
  if (!USE_MOCKS) return null;
  const s = params().get('state');
  return s === 'loading' || s === 'empty' || s === 'error' || s === 'offline' ? s : null;
}

export function urlNumber(name: string): number | null {
  if (!USE_MOCKS) return null;
  const v = params().get(name);
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function urlString(name: string): string | null {
  return USE_MOCKS ? params().get(name) : null;
}
