/**
 * Supabase Auth for live mode (docs/supabase.md §4). One sign-in page for everyone; `profiles.role`
 * decides where the account lands: operators in the cab (/operator), managers and admins in the
 * office (/manager). Mock mode (VITE_USE_MOCKS=true) skips all of this, as before.
 */
import { useEffect, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { create } from 'zustand';
import { client, currentApp, signInClient, type App, type Db } from '@/lib/supabase';
import { useSession } from '@/stores/session';
import type { Tables } from '@/types/supabase';
import { USE_MOCKS } from './config';

export type Profile = Pick<Tables<'profiles'>, 'id' | 'role' | 'full_name' | 'operator_id' | 'site_id' | 'preferred_language'>;

interface Slot {
  status: 'loading' | 'signed_out' | 'signed_in';
  profile: Profile | null;
}
type AuthState = Record<App, Slot>;

const initial: Slot = USE_MOCKS ? { status: 'signed_in', profile: null } : { status: 'loading', profile: null };
export const useAuth = create<AuthState>(() => ({ cab: initial, office: initial }));

/** The auth state of this tab's app (cab for /operator…, office otherwise). */
export function useAuthSlot(): Slot {
  const location = useLocation();
  const app: App = location.pathname.startsWith('/operator') ? 'cab' : 'office';
  return useAuth((s) => s[app]);
}

export function homeFor(role: Profile['role']): string {
  return role === 'operator' ? '/operator/handover' : '/manager';
}

// Profiles are cached per app so a cab that starts offline still knows who is signed in.
const PROFILE_KEY = (app: App) => `cat-${app}-profile`;
function cachedProfile(app: App, userId: string): Profile | null {
  try {
    const p = JSON.parse(window.localStorage.getItem(PROFILE_KEY(app)) ?? 'null') as Profile | null;
    return p?.id === userId ? p : null;
  } catch {
    return null;
  }
}
function cacheProfile(app: App, p: Profile | null) {
  try {
    if (p) window.localStorage.setItem(PROFILE_KEY(app), JSON.stringify(p));
    else window.localStorage.removeItem(PROFILE_KEY(app));
  } catch {
    /* private mode */
  }
}

async function fetchProfile(db: Db, userId: string): Promise<Profile | null> {
  const { data, error } = await db
    .from('profiles')
    .select('id, role, full_name, operator_id, site_id, preferred_language')
    .eq('id', userId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return data;
}

const set = (app: App, slot: Slot) => useAuth.setState({ [app]: slot } as Partial<AuthState>);

async function resolve(app: App, db: Db, userId: string) {
  try {
    const p = await fetchProfile(db, userId);
    cacheProfile(app, p);
    set(app, p ? { status: 'signed_in', profile: p } : { status: 'signed_out', profile: null });
  } catch {
    const p = cachedProfile(app, userId); // offline: last known profile
    set(app, p ? { status: 'signed_in', profile: p } : { status: 'signed_out', profile: null });
  }
}

let booted = false;

/** Follows both apps' sessions. Mount once (App). */
export function useAuthBoot(): void {
  useEffect(() => {
    if (USE_MOCKS || booted) return;
    booted = true;
    for (const app of ['cab', 'office'] as const) {
      const db = client(app);
      if (!db) {
        set(app, { status: 'signed_out', profile: null });
        continue;
      }
      db.auth.onAuthStateChange((event, session) => {
        if (!session) {
          cacheProfile(app, null);
          set(app, { status: 'signed_out', profile: null });
          if (app === 'cab') useSession.getState().signOut();
          return;
        }
        // Supabase calls must not run inside this callback (auth lock): defer.
        if (event === 'INITIAL_SESSION' || event === 'SIGNED_IN' || event === 'USER_UPDATED') {
          window.setTimeout(() => void resolve(app, db, session.user.id), 0);
        }
      });
    }
  }, []);
}

/**
 * Signs in with email + password and returns the profile. The session is handed to the client of
 * the app the role belongs to, so it doesn't matter which page the sign-in happened on.
 */
export async function signInWithRole(email: string, password: string): Promise<Profile> {
  const tmp = signInClient();
  if (!tmp) throw new Error('Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.');
  const { data, error } = await tmp.auth.signInWithPassword({ email, password });
  if (error || !data.session) throw new Error(error?.message === 'Invalid login credentials' ? 'Wrong email or password.' : (error?.message ?? 'Sign-in failed.'));
  const profile = await fetchProfile(tmp, data.user.id);
  if (!profile) throw new Error('This account has no profile yet. Ask the site admin.');
  const app: App = profile.role === 'operator' ? 'cab' : 'office';
  const { error: e2 } = await client(app)!.auth.setSession({
    access_token: data.session.access_token,
    refresh_token: data.session.refresh_token,
  });
  if (e2) throw new Error(e2.message);
  cacheProfile(app, profile);
  set(app, { status: 'signed_in', profile });
  return profile;
}

export async function signOut(app: App = currentApp()): Promise<void> {
  if (app === 'cab') useSession.getState().signOut();
  if (USE_MOCKS) return;
  await client(app)?.auth.signOut({ scope: 'local' });
}

/** The access token of this tab's app, refreshed if it is about to expire. */
export async function accessToken(app: App = currentApp()): Promise<string | null> {
  const db = client(app);
  if (!db) return null;
  const { data } = await db.auth.getSession();
  return data.session?.access_token ?? null;
}

/** After a 401: force a refresh and return the new token (null when the session is gone). */
export async function refreshToken(app: App = currentApp()): Promise<string | null> {
  const db = client(app);
  if (!db) return null;
  const { data } = await db.auth.refreshSession();
  return data.session?.access_token ?? null;
}

function Waiting() {
  return <div className="min-h-full bg-bg" aria-busy="true" />;
}

/** Office routes (/manager, /demo): managers and admins only in live mode; mocks pass through. */
export function RequireOffice({ children }: { children?: ReactNode }) {
  const slot = useAuthSlot();
  const location = useLocation();
  if (USE_MOCKS) return <>{children ?? <Outlet />}</>;
  if (slot.status === 'loading') return <Waiting />;
  if (slot.status === 'signed_out' || !slot.profile) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  if (slot.profile.role === 'operator') return <Navigate to="/operator" replace />;
  return <>{children ?? <Outlet />}</>;
}

/** `/`: mocks open the cab as before; live mode sends a signed-in manager to the office, else sign-in. */
export function Home() {
  const office = useAuth((s) => s.office);
  const cab = useSession((s) => s.cab);
  if (USE_MOCKS || cab) return <Navigate to="/operator" replace />;
  if (office.status === 'loading') return <Waiting />;
  if (office.status === 'signed_in' && office.profile) return <Navigate to={homeFor(office.profile.role)} replace />;
  return <Navigate to="/login" replace />;
}
