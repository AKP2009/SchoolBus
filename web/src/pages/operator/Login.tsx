import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { CloudOff, Languages, LogIn, Moon, Sun, Truck } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { Skeleton } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { homeFor, useAuth } from '@/data/auth';
import { USE_MOCKS } from '@/data/config';
import { signIn, useWorld } from '@/data/hooks';
import { LANGS, setLanguage, useLanguage, useT } from '@/i18n';
import { CabLayout } from '@/layouts/CabLayout';
import { fmtTime } from '@/lib/format';
import { unlockAudio } from '@/lib/tones';
import { useConnection } from '@/stores/connection';
import { useSession } from '@/stores/session';

const field =
  'min-h-touch-cab w-full rounded-md border border-line bg-raised px-4 text-cab-body text-ink placeholder:text-ink-3 focus:border-saffron-500';

/**
 * One sign-in for everyone (/login and /operator/login). Mock mode signs the cab in as the demo
 * operator, as before; live mode uses Supabase Auth and sends operators to the cab and managers to
 * the office (profiles.role).
 */
export function OperatorLogin() {
  const t = useT();
  const cab = useSession((s) => s.cab);
  const office = useAuth((s) => s.office);
  const online = useConnection((s) => s.online);
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const next = params.get('next');
  const [email, setEmail] = useState(USE_MOCKS ? 'ganesh@demo.site' : '');
  const [password, setPassword] = useState(USE_MOCKS ? 'demo1234' : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // /operator/login is the cab's own page: a signed-in cab goes straight back to work. /login is shared
  // (a manager may sign in on a laptop whose cab tab is signed in), so it only skips the form in mocks.
  const cabPage = location.pathname.startsWith('/operator');
  if (cab && (cabPage || USE_MOCKS) && !next) return <Navigate to={cab.handoverSeen ? '/operator' : '/operator/handover'} replace />;
  if (!USE_MOCKS && !cabPage && !next && office.status === 'signed_in' && office.profile && office.profile.role !== 'operator') return <Navigate to="/manager" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    unlockAudio(); // first tap: allow alert tones from now on
    setBusy(true);
    setError(null);
    try {
      const role = await signIn(email.trim(), password);
      navigate(role === 'operator' ? homeFor(role) : next && !next.startsWith('/operator') ? next : homeFor(role));
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <CabLayout
      bare
      status={{ machineId: cab?.machineId ?? '—', shiftMinutes: 0, fuelPct: null }}
      safety={null}
      primary={
        <div className="mx-auto grid w-full max-w-[1100px] flex-1 grid-cols-2 items-center gap-12">
          <section className="flex flex-col gap-6" aria-label={t('login.machineAria')}>
            <p className="text-cab-h2 text-ink-2">{t('login.brand')}</p>
            <h1 className="text-cab-h1">{t('login.title')}</h1>
            {USE_MOCKS ? <MockShift /> : <p className="text-cab-body text-ink-2">{t('login.liveHint')}</p>}
            <LanguagePicker />
          </section>

          <form onSubmit={submit} className="flex flex-col gap-4 rounded-lg border bg-surface p-8" aria-label={t('login.formAria')}>
            <label className="flex flex-col gap-2">
              <span className="text-cab-small text-ink-2">{t('login.email')}</span>
              <input className={field} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-cab-small text-ink-2">{t('login.password')}</span>
              <input className={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && (
              <p role="alert" className="rounded-md border border-critical bg-tint-critical px-4 py-3 text-cab-body">
                {error}
              </p>
            )}
            {!online && !USE_MOCKS && (
              <p className="flex items-center gap-2 text-cab-small text-ink-2">
                <CloudOff size={24} aria-hidden /> {t('login.needConnection')}
              </p>
            )}
            <Button type="submit" icon={LogIn} fullWidth disabled={busy || (!online && !USE_MOCKS)} className="mt-2">
              {busy ? t('login.signingIn') : t('login.submit')}
            </Button>
            {USE_MOCKS && <p className="text-cab-small text-ink-2">{t('login.mockHint')}</p>}
          </form>
        </div>
      }
    />
  );
}

/** Mock mode: the demo operator's machine and shift, as before sign-in. */
function MockShift() {
  const t = useT();
  const world = useWorld();
  return (
    <DataState
      res={world}
      errorTitle={t('login.loadError')}
      empty={{ title: t('login.noShift'), hint: t('login.noShiftHint') }}
      skeleton={<Skeleton className="h-40" />}
    >
      {(w) => (
        <div className="flex flex-col gap-4 rounded-md border bg-surface p-6">
          <div className="flex items-center gap-4">
            <Truck size={40} className="text-ink-2" aria-hidden />
            <div>
              <p className="text-cab-h2">
                <span className="reading">{w.machine.machine_id}</span> · {w.machine.model} {t(`machineType.${w.machine.machine_type}`).toLowerCase()}
              </p>
              <p className="text-cab-small text-ink-2">{w.site.name}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            {w.shift.shift_type === 'night' ? <Moon size={40} className="text-ink-2" aria-hidden /> : <Sun size={40} className="text-ink-2" aria-hidden />}
            <p className="text-cab-body">
              {t(`shift.${w.shift.shift_type}`)}{' '}
              <span className="reading">
                {fmtTime(w.shift.start_time)}–{fmtTime(w.shift.end_time)}
              </span>
            </p>
          </div>
          <StatusBadge status="ok" label={t('login.assignedTo', { name: w.operator.full_name })} />
        </div>
      )}
    </DataState>
  );
}

/** English / हिन्दी / தமிழ் for the cab. Sign-in also applies profiles.preferred_language. */
function LanguagePicker() {
  const t = useT();
  const lang = useLanguage();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 flex items-center gap-2 text-cab-small text-ink-2">
        <Languages size={24} aria-hidden /> {t('lang.label')}
      </legend>
      <div className="flex gap-3">
        {LANGS.map((l) => (
          <Button key={l} type="button" variant={lang === l ? 'primary' : 'secondary'} aria-pressed={lang === l} onClick={() => setLanguage(l)} lang={l}>
            {t(`lang.${l}`)}
          </Button>
        ))}
      </div>
    </fieldset>
  );
}
