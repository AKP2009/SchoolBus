import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CloudOff, Droplets, Eye, Flame, Languages, Mountain, Pause, Play, RotateCcw, ShieldAlert, Siren, Square, UserRound, Wifi } from 'lucide-react';
import { useShallow } from 'zustand/react/shallow';
import { Button } from '@/components/Button';
import { StatusBadge } from '@/components/StatusBadge';
import { useAuth } from '@/data/auth';
import { DEMO_REPLAY, USE_MOCKS } from '@/data/config';
import { sendDemo } from '@/data/demoBus';
import { useWorld } from '@/data/hooks';
import { mockReplay, setReplayStatus, useLive, useLiveStream } from '@/data/live';
import * as api from '@/data/api';
import { LANGS, useLanguage } from '@/i18n';
import { fmtTime } from '@/lib/format';
import { ModeProvider, useDocumentMode } from '@/lib/mode';
import { useConnection } from '@/stores/connection';
import type { ReplayStatus, StreamMessage } from '@/types/domain';

type Scenario = 'overheating' | 'hydraulic_leak' | 'tip_risk' | 'seatbelt' | 'fatigue' | 'proximity' | 'sos';

const SCENARIOS: Array<{ name: Scenario; label: string; icon: typeof Flame; note: string }> = [
  { name: 'overheating', label: 'Overheating', icon: Flame, note: 'Coolant climbs to 112 °C: warn → derate → safe shutdown → escalate' },
  { name: 'hydraulic_leak', label: 'Hydraulic leak', icon: Droplets, note: 'Pressure drops 55% under load' },
  { name: 'tip_risk', label: 'Tip risk', icon: Mountain, note: 'Roll rises to 27° on a side slope' },
  { name: 'seatbelt', label: 'Seatbelt', icon: ShieldAlert, note: 'Moving unbuckled for 3 minutes' },
  { name: 'fatigue', label: 'Fatigue', icon: Eye, note: 'Fatigue rises to high' },
  { name: 'proximity', label: 'Person behind', icon: UserRound, note: 'Person in the rear sector, 2.4 m' },
  { name: 'sos', label: 'SOS', icon: Siren, note: 'Emergency from the cab' },
];

// Recorded events in the mock stream to jump to (data seconds before the event).
const RECORDED: Partial<Record<Scenario, { find: (m: StreamMessage) => boolean; lead: number }>> = {
  overheating: { find: (m) => m.kind === 'alert' && m.data.alert_code.startsWith('COOLANT'), lead: 300 },
  seatbelt: { find: (m) => m.kind === 'alert' && m.data.alert_code === 'SEATBELT', lead: 30 },
  fatigue: { find: (m) => m.kind === 'safety' && m.data.type === 'fatigue_high', lead: 240 },
  proximity: { find: (m) => m.kind === 'safety' && (m.data.type === 'blindspot_intrusion' || m.data.type === 'proximity_breach'), lead: 20 },
};

/** Client-side stand-ins for scenarios the mock stream didn't record (thresholds.yaml wording). */
function simulated(name: Scenario): Array<StreamMessage & { offsetS: number }> {
  const id = -Math.floor(Date.now() / 1000);
  const a = (offsetS: number, severity: 'warning' | 'critical', stage: 'warn' | 'derate' | 'recommend_shutdown' | 'escalated' | 'resolved', code: string, title: string, action: string) =>
    ({ offsetS, kind: 'alert' as const, data: { id, alert_code: code, severity, stage, title, recommended_action: action } });
  if (name === 'hydraulic_leak')
    return [
      a(0, 'critical', 'warn', 'HYD_PRESSURE_DROP', 'Hydraulic pressure down 55% — possible leak', 'Stop moving the attachment. Check for oil under the machine.'),
      a(120, 'critical', 'derate', 'HYD_PRESSURE_DROP', 'Hydraulic pressure down 55% — possible leak', 'Switch to economy mode and reduce load.'),
      a(300, 'critical', 'recommend_shutdown', 'HYD_PRESSURE_DROP', 'Hydraulic pressure down 55% — possible leak', "Lower the attachment to the ground, shut down and don't restart until it's checked."),
      a(420, 'critical', 'escalated', 'HYD_PRESSURE_DROP', 'Hydraulic pressure down 55% — possible leak', 'Stop work safely and shut down. Your site manager has been told.'),
      a(600, 'critical', 'resolved', 'HYD_PRESSURE_DROP', 'Hydraulic pressure restored', 'Back to normal. You can carry on.'),
    ];
  return [
    a(0, 'warning', 'warn', 'TIP_RISK', 'Machine tilting — 17°', 'Slow down. Keep the attachment low and move to flatter ground.'),
    a(120, 'critical', 'warn', 'TIP_RISK', 'Machine tilting — 26°', 'Slow down. Keep the attachment low and move to flatter ground.'),
    a(240, 'critical', 'escalated', 'TIP_RISK', 'Machine tilting — 27°', 'Slow down. Keep the attachment low and move to flatter ground. Your site manager has been told.'),
    a(540, 'critical', 'resolved', 'TIP_RISK', 'Machine level again', 'Back to normal. You can carry on.'),
  ];
}

export function Demo() {
  useDocumentMode('office');
  const world = useWorld();
  const manager = useAuth((s) => s.office.profile);
  const lang = useLanguage();
  // Mock: the recorded stream's machine. Live: the scenario target (M05, Ganesh's dozer, by default).
  const [target, setTarget] = useState(DEMO_REPLAY.machineId);
  const machineId = USE_MOCKS ? (world.data?.machine.machine_id ?? null) : target.trim() || null;
  useLiveStream(USE_MOCKS ? machineId : null);
  const [t, endS, playing, speed, fromMs, clock] = useLive(useShallow((s) => [s.t, s.endS, s.playing, s.speed, s.fromMs, s.clock] as const));
  const offline = useConnection((s) => s.forcedOffline);
  const [log, setLog] = useState<string[]>([]);
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [from, setFrom] = useState(DEMO_REPLAY.from);
  const [ids, setIds] = useState(DEMO_REPLAY.machineIds.join(','));
  const [realSpeed, setRealSpeed] = useState(DEMO_REPLAY.speed);

  const note = (s: string) => setLog((l) => [`${new Date().toLocaleTimeString()} ${s}`, ...l].slice(0, 12));

  useEffect(() => {
    if (USE_MOCKS) return;
    const poll = () =>
      void api
        .replayStatus()
        .then((s) => {
          setStatus(s);
          setReplayStatus(s);
        })
        .catch(() => setStatus(null));
    poll();
    const id = window.setInterval(poll, 5000);
    return () => window.clearInterval(id);
  }, []);

  const run = async (name: Scenario) => {
    if (!machineId) return;
    if (!USE_MOCKS) {
      try {
        if (name === 'fatigue' || name === 'proximity' || name === 'sos') {
          // These come from the vision service / voice in the real system: post the event it would send.
          const base = { machine_id: machineId, operator_id: DEMO_REPLAY.operatorId, ts: new Date().toISOString() };
          const body =
            name === 'proximity'
              ? { ...base, type: 'proximity_breach', severity: 'critical', distance_m: 2.4, sector: 'rear', approaching: true, details: { class: 'person', conf: 0.9 } }
              : name === 'fatigue'
                ? { ...base, type: 'fatigue_high', severity: 'warning', sector: 'cab', details: { fatigue_level: 'high', fatigue_score: 0.66 } }
                : { ...base, type: 'sos', severity: 'emergency' };
          const r = await api.postEvent(body);
          note(`${name}: alert ${r.alert_id}`);
        } else {
          const r = await api.scenario(name, machineId);
          note(`${name} started at ${fmtTime(r.start_ts)} for ${r.duration_min} min`);
        }
      } catch (e) {
        note(`${name} failed: ${e instanceof Error ? e.message : String(e)}`);
      }
      return;
    }
    if (name === 'sos') {
      sendDemo({ type: 'sos' });
      note('SOS raised in the cab');
      return;
    }
    const rec = RECORDED[name];
    const at = rec ? mockReplay.find((m) => rec.find(m)) : null;
    if (rec && at != null) {
      sendDemo({ type: 'seek', t: Math.max(0, at - rec.lead) });
      sendDemo({ type: 'play' });
      note(`${name}: jumped to the recorded event (${Math.round(rec.lead / 60)} min before it)`);
    } else {
      sendDemo({ type: 'inject', items: simulated(name) });
      note(`${name}: simulated in the browser (not in the recorded stream)`);
    }
  };

  const pos = (s: number) => fmtTime(fromMs + s * 1000);

  return (
    <ModeProvider mode="office">
      <div className="min-h-full bg-bg p-6 text-ink">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-office-h1">Demo panel</h1>
            <p className="text-office-body text-ink-2">
              Controls every open tab of this app.{' '}
              {USE_MOCKS ? 'Mock mode: replays the recorded stream in the browser.' : `Backend mode: calls /replay and /scenario as ${manager?.full_name ?? 'the signed-in manager'}.`}
            </p>
          </div>
          <nav className="flex gap-4 text-office-body">
            <Link className="underline" to="/operator" target="_blank">
              Operator tab
            </Link>
            <Link className="underline" to="/manager" target="_blank">
              Manager tab
            </Link>
          </nav>
        </header>

        <div className="grid grid-cols-12 gap-6">
          <section className="col-span-7 flex flex-col gap-4 rounded-md border bg-surface p-4" aria-label="Replay">
            <h2 className="text-office-h2">Replay</h2>
            {USE_MOCKS ? (
              <>
                <p className="text-office-body">
                  <span className="reading">{machineId}</span> · data time <span className="reading">{clock ? fmtTime(clock) : '—'}</span> ·{' '}
                  <span className="reading">{Math.round(t / 60)}</span> of <span className="reading">{Math.round(endS / 60)}</span> min ·{' '}
                  {playing ? 'playing' : 'paused'} at <span className="reading">{speed}×</span>
                </p>
                <input
                  type="range"
                  min={0}
                  max={endS}
                  step={30}
                  value={t}
                  onChange={(e) => sendDemo({ type: 'seek', t: Number(e.target.value) })}
                  aria-label="Replay position"
                  className="w-full accent-[var(--saffron-500)]"
                />
                <div className="flex justify-between text-office-small text-ink-2">
                  <span className="reading">{pos(0)}</span>
                  <span className="reading">{pos(endS)}</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {playing ? (
                    <Button variant="secondary" icon={Pause} onClick={() => sendDemo({ type: 'pause' })}>
                      Pause
                    </Button>
                  ) : (
                    <Button icon={Play} onClick={() => sendDemo({ type: 'play' })}>
                      Play
                    </Button>
                  )}
                  {[1, 10, 60].map((v) => (
                    <Button key={v} variant={speed === v ? 'primary' : 'secondary'} aria-pressed={speed === v} onClick={() => sendDemo({ type: 'speed', speed: v })}>
                      {v}×
                    </Button>
                  ))}
                  <Button
                    variant="ghost"
                    icon={RotateCcw}
                    onClick={() => {
                      sendDemo({ type: 'reset' });
                      sendDemo({ type: 'seek', t: 60 });
                      note('Restarted from the beginning');
                    }}
                  >
                    Restart
                  </Button>
                </div>
              </>
            ) : (
              <>
                <div className="grid grid-cols-3 gap-3">
                  <label className="col-span-2 flex flex-col gap-1 text-office-small text-ink-2">
                    Machines
                    <input className="min-h-touch-office rounded-sm border px-2 font-mono text-office-body text-ink" value={ids} onChange={(e) => setIds(e.target.value)} />
                  </label>
                  <label className="flex flex-col gap-1 text-office-small text-ink-2">
                    Speed
                    <select className="min-h-touch-office rounded-sm border px-2 text-office-body text-ink" value={realSpeed} onChange={(e) => setRealSpeed(Number(e.target.value))}>
                      {[1, 10, 60].map((v) => (
                        <option key={v} value={v}>
                          {v}×
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="col-span-3 flex flex-col gap-1 text-office-small text-ink-2">
                    From (UTC; Supabase holds 2026-08-16 → 08-29)
                    <input className="min-h-touch-office rounded-sm border px-2 font-mono text-office-body text-ink" value={from} onChange={(e) => setFrom(e.target.value)} />
                  </label>
                </div>
                <div className="flex gap-2">
                  <Button
                    icon={Play}
                    onClick={() =>
                      void api
                        .replayStart({ machine_ids: ids.split(',').map((s) => s.trim()).filter(Boolean), from, speed: realSpeed })
                        .then((s) => {
                          setStatus(s);
                          setReplayStatus(s);
                          note(`Replay started: ${s.machine_ids.join(', ')} from ${fmtTime(s.replay_ts)} at ${s.speed}×`);
                        })
                        .catch((e: Error) => note(`Start failed: ${e.message}`))
                    }
                  >
                    Start replay
                  </Button>
                  <Button
                    variant="secondary"
                    icon={Square}
                    onClick={() =>
                      void api
                        .replayStop()
                        .then((s) => {
                          setStatus(s);
                          setReplayStatus(s);
                          note('Replay stopped');
                        })
                        .catch((e: Error) => note(`Stop failed: ${e.message}`))
                    }
                  >
                    Stop
                  </Button>
                </div>
                <p className="text-office-body">
                  {status ? (
                    <>
                      <StatusBadge status={status.running ? 'ok' : 'offline'} label={status.running ? 'Running' : 'Stopped'} /> {status.machine_ids.join(', ')} · data time{' '}
                      <span className="reading">{fmtTime(status.replay_ts)}</span> · source {status.source ?? '—'}
                    </>
                  ) : (
                    <StatusBadge status="offline" label="Backend not reachable" />
                  )}
                </p>
              </>
            )}
          </section>

          <section className="col-span-5 flex flex-col gap-3 rounded-md border bg-surface p-4" aria-label="Connection">
            <h2 className="text-office-h2">Connection</h2>
            <p className="text-office-body text-ink-2">Makes every tab behave as offline: reports queue, then sync when you switch back.</p>
            <Button variant={offline ? 'primary' : 'secondary'} icon={offline ? Wifi : CloudOff} onClick={() => sendDemo({ type: 'offline', on: !offline })}>
              {offline ? 'Go back online' : 'Go offline'}
            </Button>
            <h2 className="mt-2 flex items-center gap-2 text-office-h2">
              <Languages size={20} aria-hidden /> Cab language
            </h2>
            <div className="flex gap-2">
              {LANGS.map((l) => (
                <Button key={l} variant={lang === l ? 'primary' : 'secondary'} aria-pressed={lang === l} onClick={() => sendDemo({ type: 'lang', lang: l })} lang={l}>
                  {l === 'en' ? 'English' : l === 'hi' ? 'हिन्दी' : 'தமிழ்'}
                </Button>
              ))}
            </div>
            <h2 className="mt-2 text-office-h2">Log</h2>
            <ul className="reading flex flex-col gap-1 text-office-small text-ink-2" aria-live="polite">
              {log.length === 0 && <li>Nothing yet.</li>}
              {log.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          </section>

          <section className="col-span-12 flex flex-col gap-3 rounded-md border bg-surface p-4" aria-label="Scenarios">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h2 className="text-office-h2">Scenarios on {machineId ?? '…'}</h2>
              {!USE_MOCKS && (
                <label className="flex items-center gap-2 text-office-small text-ink-2">
                  Machine
                  <input className="min-h-touch-office w-24 rounded-sm border px-2 font-mono text-office-body text-ink" value={target} onChange={(e) => setTarget(e.target.value.toUpperCase())} />
                </label>
              )}
            </div>
            <div className="grid grid-cols-4 gap-3">
              {SCENARIOS.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => void run(s.name)}
                  className="flex min-h-touch-office flex-col items-start gap-1 rounded-md border bg-surface p-3 text-left transition-state hover:bg-raised"
                >
                  <span className="flex items-center gap-2 text-office-h3">
                    <s.icon size={20} aria-hidden /> {s.label}
                  </span>
                  <span className="text-office-small text-ink-2">{s.note}</span>
                  {USE_MOCKS && !RECORDED[s.name] && s.name !== 'sos' && <span className="text-office-small text-ink-2">Simulated in mock mode</span>}
                </button>
              ))}
            </div>
            <p className="text-office-small text-ink-2">Scenarios never stop the machine: the cab gets the graded response.</p>
          </section>
        </div>
      </div>
    </ModeProvider>
  );
}
