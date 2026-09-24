import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { CloudOff, LogIn, Moon, Sun, Truck } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { Skeleton } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { USE_MOCKS } from '@/data/config';
import { signInOperator, useWorld } from '@/data/hooks';
import { CabLayout } from '@/layouts/CabLayout';
import { fmtTime, MACHINE_WORD } from '@/lib/format';
import { unlockAudio } from '@/lib/tones';
import { useConnection } from '@/stores/connection';
import { useSession } from '@/stores/session';

const field =
  'min-h-touch-cab w-full rounded-md border border-line bg-raised px-4 text-cab-body text-ink placeholder:text-ink-3 focus:border-saffron-500';

export function OperatorLogin() {
  const cab = useSession((s) => s.cab);
  const world = useWorld();
  const online = useConnection((s) => s.online);
  const navigate = useNavigate();
  const [email, setEmail] = useState(USE_MOCKS ? 'ganesh@demo.site' : '');
  const [password, setPassword] = useState(USE_MOCKS ? 'demo1234' : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (cab) return <Navigate to={cab.handoverSeen ? '/operator' : '/operator/handover'} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    unlockAudio(); // first tap: allow alert tones from now on
    setBusy(true);
    setError(null);
    try {
      await signInOperator(email.trim(), password);
      navigate('/operator/handover');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <CabLayout
      bare
      status={{ machineId: world.data?.machine.machine_id ?? '—', shiftMinutes: 0, fuelPct: null }}
      safety={null}
      primary={
        <div className="mx-auto grid w-full max-w-[1100px] flex-1 grid-cols-2 items-center gap-12">
          <section className="flex flex-col gap-6" aria-label="Your machine and shift">
            <p className="text-cab-h2 text-ink-2">Smart Operator</p>
            <h1 className="text-cab-h1">Sign in to start your shift.</h1>
            <DataState
              res={world}
              errorTitle="Couldn't load today's shift."
              empty={{ title: 'No shift assigned yet.', hint: 'Your supervisor assigns the machine and shift.' }}
              skeleton={<Skeleton className="h-40" />}
            >
              {(w) => (
                <div className="flex flex-col gap-4 rounded-md border bg-surface p-6">
                  <div className="flex items-center gap-4">
                    <Truck size={40} className="text-ink-2" aria-hidden />
                    <div>
                      <p className="text-cab-h2">
                        <span className="reading">{w.machine.machine_id}</span> · {w.machine.model} {MACHINE_WORD[w.machine.machine_type].toLowerCase()}
                      </p>
                      <p className="text-cab-small text-ink-2">{w.site.name}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {w.shift.shift_type === 'night' ? <Moon size={40} className="text-ink-2" aria-hidden /> : <Sun size={40} className="text-ink-2" aria-hidden />}
                    <p className="text-cab-body">
                      {w.shift.shift_type === 'night' ? 'Night' : 'Day'} shift{' '}
                      <span className="reading">
                        {fmtTime(w.shift.start_time)}–{fmtTime(w.shift.end_time)}
                      </span>
                    </p>
                  </div>
                  <StatusBadge status="ok" label={`Assigned to ${w.operator.full_name}`} />
                </div>
              )}
            </DataState>
          </section>

          <form onSubmit={submit} className="flex flex-col gap-4 rounded-lg border bg-surface p-8" aria-label="Sign in">
            <label className="flex flex-col gap-2">
              <span className="text-cab-small text-ink-2">Email</span>
              <input className={field} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-cab-small text-ink-2">Password</span>
              <input className={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && (
              <p role="alert" className="rounded-md border border-critical bg-tint-critical px-4 py-3 text-cab-body">
                {error}
              </p>
            )}
            {!online && !USE_MOCKS && (
              <p className="flex items-center gap-2 text-cab-small text-ink-2">
                <CloudOff size={24} aria-hidden /> Signing in needs a connection. Move to where the site Wi-Fi reaches.
              </p>
            )}
            <Button type="submit" icon={LogIn} fullWidth disabled={busy || (!online && !USE_MOCKS)} className="mt-2">
              {busy ? 'Signing in…' : 'Sign in and start shift'}
            </Button>
            {USE_MOCKS && <p className="text-cab-small text-ink-2">Demo data: any password of 4 or more characters works.</p>}
          </form>
        </div>
      }
    />
  );
}
