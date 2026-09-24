/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  readonly VITE_API_URL?: string;
  readonly VITE_WS_URL?: string;
  /** 'false' switches the data hooks from web/src/mocks to Supabase + FastAPI. */
  readonly VITE_USE_MOCKS?: string;
  /** Live mode: data time to use when no replay is running (e.g. 2026-08-19T15:15:00Z). */
  readonly VITE_DATA_NOW?: string;
}
