import { useMemo, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { KpiTile } from '@/components/KpiTile';
import { useSafetyEvents, useShifts } from '@/data/hooks';
import { fmtDate, SAFETY_WORD } from '@/lib/format';
import type { SafetyEventRow, SafetyEventType, ShiftRow } from '@/types/domain';
import { ManagerShell, useOfficeNow, useSiteId } from './Shell';

// Two series, first two slots of the validated categorical order (see Clusters.tsx).
const DAY = 'var(--series-2)';
const NIGHT = 'var(--saffron-600)';
const HOURS = 8;

interface Placed extends SafetyEventRow {
  hour: number;
  shiftType: 'day' | 'night';
}

/** Put each event in its shift (same machine, start ≤ ts < end) and its hour of shift (1–8). */
function place(events: SafetyEventRow[], shifts: ShiftRow[]): Placed[] {
  const byMachine = new Map<string, ShiftRow[]>();
  for (const s of shifts) {
    const list = byMachine.get(s.machine_id) ?? [];
    list.push(s);
    byMachine.set(s.machine_id, list);
  }
  const out: Placed[] = [];
  for (const e of events) {
    const t = Date.parse(e.ts);
    const s = (byMachine.get(e.machine_id ?? '') ?? []).find((x) => Date.parse(x.start_time) <= t && t < Date.parse(x.end_time));
    if (!s) continue;
    const hour = Math.min(HOURS, Math.floor((t - Date.parse(s.start_time)) / 3.6e6) + 1);
    out.push({ ...e, hour, shiftType: s.shift_type });
  }
  return out;
}

export function ManagerSafety() {
  const siteId = useSiteId();
  const events = useSafetyEvents(siteId);
  const shifts = useShifts(siteId);
  const now = useOfficeNow();
  const [type, setType] = useState<SafetyEventType | 'all'>('all');

  const placed = useMemo(
    () => (events.data && shifts.data ? place(events.data.filter((e) => Date.parse(e.ts) <= now), shifts.data) : []),
    [events.data, shifts.data, now],
  );
  const types = useMemo(() => {
    const counts = new Map<SafetyEventType, number>();
    for (const e of placed) counts.set(e.event_type, (counts.get(e.event_type) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [placed]);
  const shown = type === 'all' ? placed : placed.filter((e) => e.event_type === type);
  const data = Array.from({ length: HOURS }, (_, i) => ({
    hour: `${i + 1}`,
    Day: shown.filter((e) => e.hour === i + 1 && e.shiftType === 'day').length,
    Night: shown.filter((e) => e.hour === i + 1 && e.shiftType === 'night').length,
  }));
  const nightShare = shown.length ? shown.filter((e) => e.shiftType === 'night').length / shown.length : null;
  const lateShare = shown.length ? shown.filter((e) => e.hour >= 5).length / shown.length : null;
  const first = placed.length ? placed.reduce((m, e) => (e.ts < m ? e.ts : m), placed[0]!.ts) : null;

  return (
    <ManagerShell title="Safety analytics">
      <DataState
        res={{ ...events, status: shifts.status === 'loading' ? 'loading' : events.status, data: events.data && shifts.data ? placed : undefined }}
        errorTitle="Couldn't load safety events."
        empty={{ icon: ShieldCheck, title: 'No safety events yet.', hint: 'Seatbelt, proximity, tilt and fatigue events from every machine appear here.' }}
        className="col-span-12"
      >
        {() => (
          <>
            <KpiTile className="col-span-3" label="Safety events" value={shown.length} unit={first ? `since ${fmtDate(first)}` : undefined} />
            <KpiTile className="col-span-3" label="On night shifts" value={nightShare == null ? '—' : Math.round(nightShare * 100)} unit="%" />
            <KpiTile className="col-span-3" label="In hours 5–8 of a shift" value={lateShare == null ? '—' : Math.round(lateShare * 100)} unit="%" />
            <KpiTile className="col-span-3" label={types[0] ? `Most common: ${SAFETY_WORD[types[0][0]].toLowerCase()}` : 'Most common'} value={types[0] ? types[0][1] : '—'} unit={types[0] ? 'events' : undefined} />

            <div className="col-span-12 flex flex-wrap gap-2" role="group" aria-label="Event type">
              <Button variant={type === 'all' ? 'primary' : 'secondary'} aria-pressed={type === 'all'} onClick={() => setType('all')}>
                All
              </Button>
              {types.map(([t, n]) => (
                <Button key={t} variant={type === t ? 'primary' : 'secondary'} aria-pressed={type === t} onClick={() => setType(t)}>
                  {SAFETY_WORD[t]} <span className="reading ml-1">{n}</span>
                </Button>
              ))}
            </div>

            <figure className="col-span-8 rounded-md border bg-surface p-4">
              <figcaption className="mb-2 text-office-h2">Events by hour of shift</figcaption>
              <div className="h-[340px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data} margin={{ top: 8, right: 16, bottom: 20, left: 0 }} barGap={2}>
                    <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                    <XAxis dataKey="hour" stroke="var(--text-2)" tick={{ fontSize: 13 }} label={{ value: 'Hour of shift', position: 'insideBottom', offset: -10, fill: 'var(--text-2)', fontSize: 13 }} />
                    <YAxis allowDecimals={false} stroke="var(--text-2)" tick={{ fontSize: 13 }} label={{ value: 'Events', angle: -90, position: 'insideLeft', fill: 'var(--text-2)', fontSize: 13 }} />
                    <Tooltip
                      cursor={{ fill: 'var(--raised)' }}
                      labelFormatter={(h) => `Hour ${h} of shift`}
                      contentStyle={{ fontFamily: 'var(--font-mono)', background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                    />
                    <Legend verticalAlign="top" height={28} iconType="square" formatter={(v) => <span className="text-office-body text-ink">{v} shift</span>} />
                    <Bar dataKey="Day" fill={DAY} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                    <Bar dataKey="Night" fill={NIGHT} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </figure>

            <section className="col-span-4 rounded-md border bg-surface p-4" aria-label="By type">
              <h2 className="mb-2 text-office-h2">By type</h2>
              <table className="w-full text-office-body">
                <thead className="text-left text-office-small text-ink-2">
                  <tr>
                    <th className="py-1 font-normal">Event</th>
                    <th className="py-1 text-right font-normal">Day</th>
                    <th className="py-1 text-right font-normal">Night</th>
                  </tr>
                </thead>
                <tbody>
                  {types.map(([t]) => (
                    <tr key={t} className="border-t">
                      <td className="py-1.5">{SAFETY_WORD[t]}</td>
                      <td className="reading py-1.5 text-right">{placed.filter((e) => e.event_type === t && e.shiftType === 'day').length}</td>
                      <td className="reading py-1.5 text-right">{placed.filter((e) => e.event_type === t && e.shiftType === 'night').length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </DataState>
    </ManagerShell>
  );
}
