import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { Database } from '@/types/supabase';

// Anon key + RLS only; the service-role key never reaches the browser (CLAUDE.md rule 4).
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export type Db = SupabaseClient<Database>;

/**
 * The operator PWA (/operator…) and the office app (/manager, /demo, /login) keep separate auth
 * sessions (own localStorage key), so Ganesh's cab tab and Priya's manager tab can run side by
 * side in one browser for the demo without signing each other out.
 */
export type App = 'cab' | 'office';

export const supabaseConfigured = !!(url && anonKey);

let pinned: App | null = null;

export function currentApp(): App {
  if (pinned) return pinned;
  return typeof window !== 'undefined' && window.location.pathname.startsWith('/operator') ? 'cab' : 'office';
}

/**
 * Runs `fn` as `app` whatever the URL: sign-in on /login (office) loads the operator's shift with the
 * cab session it just created. Only the sign-in flow uses it; nothing else runs on that page.
 */
export async function asApp<T>(app: App, fn: () => Promise<T>): Promise<T> {
  const prev = pinned;
  pinned = app;
  try {
    return await fn();
  } finally {
    pinned = prev;
  }
}

const clients: Partial<Record<App, Db>> = {};

/** The typed client of this tab's app (or of `app`); null when Supabase isn't configured (mock mode). */
export function client(app: App = currentApp()): Db | null {
  if (!url || !anonKey) return null;
  return (clients[app] ??= createClient<Database>(url, anonKey, { auth: { storageKey: `cat-${app}-auth` } }));
}

/** A client that keeps its session in memory only: sign in once, read the role, hand the session over. */
export function signInClient(): Db | null {
  if (!url || !anonKey) return null;
  return createClient<Database>(url, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false, storageKey: 'cat-signin' },
  });
}
