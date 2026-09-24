import { useMemo } from 'react';
import { Coffee, ShieldCheck } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useShallow } from 'zustand/react/shallow';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { useFatigue, useSafetyEvents } from '@/data/hooks';
import { useLive } from '@/data/live';
import { fmtTime } from '@/lib/format';
import { tiltStatus, type Status } from '@/lib/status';
import type { FatigueLevel, SeverityLevel } from '@/types/domain';
import { OperatorShell, useCab, useNow } from './Shell';
import { useT, type Key } from '@/i18n';

// Assistant behaviour by fatigue level (models.md §5).
const ADVICE: Record<FatigueLevel, { status: Status; word: Key; text: Key }> = {
  low: { status: 'ok', word: 'fatigue.low', text: 'fatigue.adviceLow' },
  medium: { status: 'warning', word: 'fatigue.medium', text: 'fatigue.adviceMedium' },
  high: { status: 'critical', word: 'fatigue.high', text: 'fatigue.adviceHigh' },
};

const SEV: Record<SeverityLevel, Status> = { info: 'info', warning: 'warning', critical: 'critical', emergency: 'emergency' };

export function OperatorSafety() {
  const t = useT();
  const cab = useCab();
  const now = useNow();
  const [telemetry, fatigue] = useLive(useShallow((s) => [s.telemetry, s.fatigue] as const));
  const fatigueRes = useFatigue(cab?.shiftId);
  const events = useSafetyEvents(cab?.siteId);

  const series = useMemo(
    () => (fatigueRes.data ?? []).filter((r) => Date.parse(r.ts) <= now).map((r) => ({ at: Date.parse(r.ts), score: r.fatigue_score })),
    [fatigueRes.data, now],
  );
  const lastLogged = fatigueRes.data?.filter((r) => Date.parse(r.ts) <= now).at(-1);
  const level: FatigueLevel = fatigue?.fatigue_level ?? lastLogged?.fatigue_level ?? 'low';
  const advice = ADVICE[level];
  const tilt = telemetry ? tiltStatus(telemetry.pitch_deg ?? 0, telemetry.roll_deg ?? 0) : 'unknown';

  return (
    <OperatorShell>
      <h1 className="text-cab-h1">{t('safety.title')}</h1>

      <section className="grid grid-cols-[1fr_auto] items-center gap-4 rounded-md border bg-surface p-6" aria-label={t('fatigue.label')}>
        <div className="flex items-start gap-4">
          {level === 'high' && <Coffee size={40} className="shrink-0 text-critical" aria-hidden />}
          <div>
            <p className="text-cab-small text-ink-2">{t('fatigue.label')}</p>
            <p className="text-cab-h2">{t(advice.text)}</p>
          </div>
        </div>
        <StatusBadge status={advice.status} label={t(advice.word)} />
      </section>

      <DataState
        res={fatigueRes}
        errorTitle={t('fatigue.loadError')}
        empty={{ title: t('fatigue.empty'), hint: t('fatigue.emptyHint') }}
        skeleton={<div className="h-44 rounded-md bg-raised" />}
      >
        {() => (
          <figure className="rounded-md border bg-surface p-4">
            <figcaption className="mb-2 text-cab-small text-ink-2">{t('fatigue.chart')}</figcaption>
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                  <XAxis dataKey="at" type="number" domain={['dataMin', 'dataMax']} scale="time" tickFormatter={(v: number) => fmtTime(v)} stroke="var(--text-2)" tick={{ fontSize: 18 }} minTickGap={80} />
                  <YAxis domain={[0, 1]} ticks={[0, 0.35, 0.6]} tickFormatter={(v: number) => (v === 0.6 ? t('fatigue.high') : v === 0.35 ? t('fatigue.medium') : t('fatigue.low'))} stroke="var(--text-2)" tick={{ fontSize: 18 }} width={84} />
                  <ReferenceLine y={0.6} stroke="var(--critical)" strokeDasharray="4 4" />
                  <ReferenceLine y={0.35} stroke="var(--warning)" strokeDasharray="4 4" />
                  <Tooltip
                    labelFormatter={(v) => fmtTime(Number(v))}
                    formatter={(v) => [Number(v).toFixed(2), t('fatigue.score')]}
                    contentStyle={{ fontFamily: 'var(--font-mono)', background: 'var(--raised)', border: '1px solid var(--border)', color: 'var(--text)' }}
                  />
                  <Line type="monotone" dataKey="score" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </figure>
        )}
      </DataState>

      <div className="grid grid-cols-2 gap-4">
        <section className="flex flex-col gap-2 rounded-md border bg-surface p-6" aria-label={t('seatbelt.label')}>
          <p className="text-cab-small text-ink-2">{t('seatbelt.label')}</p>
          {telemetry ? (
            <StatusBadge status={telemetry.seatbelt_fastened === false ? 'warning' : 'ok'} label={telemetry.seatbelt_fastened === false ? t('seatbelt.fastenNow') : t('seatbelt.fastened')} />
          ) : (
            <StatusBadge status="unknown" label={t('seatbelt.noReading')} />
          )}
        </section>
        <section className="flex flex-col gap-2 rounded-md border bg-surface p-6" aria-label={t('tilt.label')}>
          <p className="text-cab-small text-ink-2">{t('tilt.limits')}</p>
          <div className="flex items-center justify-between gap-4">
            <p className="text-cab-body">
              {t('tilt.pitch')} <span className="reading text-cab-h2">{Math.abs(telemetry?.pitch_deg ?? 0).toFixed(0)}°</span> · {t('tilt.roll')}{' '}
              <span className="reading text-cab-h2">{Math.abs(telemetry?.roll_deg ?? 0).toFixed(0)}°</span>
            </p>
            <StatusBadge status={tilt} label={tilt === 'ok' ? t('tilt.level') : tilt === 'warning' ? t('tilt.steep') : tilt === 'critical' ? t('tilt.tipRisk') : t('tilt.noReading')} />
          </div>
        </section>
      </div>

      <DataState
        res={events}
        isEmpty={(ev) => !ev.some((e) => e.machine_id === cab?.machineId && Date.parse(e.ts) >= Date.parse(cab.shiftStart) && Date.parse(e.ts) <= now)}
        errorTitle={t('events.loadError')}
        empty={{ icon: ShieldCheck, title: t('events.empty'), hint: t('events.emptyHint') }}
      >
        {(ev) => (
          <section className="flex flex-col gap-2" aria-label={t('events.thisShift')}>
            <h2 className="text-cab-h2">{t('events.thisShift')}</h2>
            <ul className="flex flex-col divide-y divide-line rounded-md border bg-surface">
              {ev
                .filter((e) => e.machine_id === cab?.machineId && Date.parse(e.ts) >= Date.parse(cab.shiftStart) && Date.parse(e.ts) <= now)
                .slice(0, 6)
                .map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-4 px-6 py-3 text-cab-body">
                    <span>
                      <span className="reading mr-3 text-ink-2">{fmtTime(e.ts)}</span>
                      {t(`event.${e.event_type}`)}
                      {e.distance_m != null && (
                        <span className="text-ink-2">
                          {' '}
                          · {e.sector ? t(`sector.${e.sector}`) : ''} <span className="reading">{e.distance_m.toFixed(1)} m</span>
                        </span>
                      )}
                    </span>
                    <StatusBadge status={SEV[e.severity]} />
                  </li>
                ))}
            </ul>
          </section>
        )}
      </DataState>
    </OperatorShell>
  );
}
