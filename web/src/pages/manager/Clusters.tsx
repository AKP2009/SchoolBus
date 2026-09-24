import { useMemo, useState } from 'react';
import { Network, SearchCheck } from 'lucide-react';
import { CartesianGrid, LabelList, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { useClusters, verifyOutlier } from '@/data/hooks';
import { cx } from '@/lib/cx';
import type { Clusters, FleetMetricRow, PcaPoint } from '@/types/domain';
import { ManagerShell } from './Shell';

// Categorical order validated with the dataviz checker on the office surface (steel, saffron-600,
// teal, violet: adjacent pairs ≥ 20 ΔE normal vision, ≥ 12 ΔE under CVD). Contrast vs white is < 3:1,
// so every cluster also has its own marker shape, a legend with words, and the table below.
const CLUSTER_COLOR = ['var(--series-2)', 'var(--saffron-600)', 'var(--series-3)', 'var(--series-4)'];
const CLUSTER_SHAPE = ['circle', 'square', 'triangle', 'diamond'] as const;
const SHAPE_SVG: Record<(typeof CLUSTER_SHAPE)[number], string> = {
  circle: 'M8 2a6 6 0 1 1 0 12A6 6 0 0 1 8 2z',
  square: 'M2.5 2.5h11v11h-11z',
  triangle: 'M8 1.5 14.5 14h-13z',
  diamond: 'M8 1 15 8l-7 7-7-7z',
};

type Kind = 'operator' | 'machine';

/** Scatter mark: the cluster's shape at `size` px, with a surface ring when emphasised. */
function mark(id: number, size: number, ring: boolean) {
  return function Mark(props: unknown) {
    const { cx = 0, cy = 0 } = props as { cx?: number; cy?: number };
    const k = size / 16;
    return (
      <path
        d={SHAPE_SVG[CLUSTER_SHAPE[id % 4]!]}
        transform={`translate(${cx - size / 2} ${cy - size / 2}) scale(${k})`}
        fill={CLUSTER_COLOR[id % 4]}
        fillOpacity={ring ? 1 : 0.3}
        stroke={ring ? 'var(--surface)' : 'none'}
        strokeWidth={ring ? 2 / k : 0}
      />
    );
  };
}

export function ManagerClusters() {
  const res = useClusters();
  const [kind, setKind] = useState<Kind>('operator');
  return (
    <ManagerShell
      title="Clusters"
      actions={
        <div className="flex gap-2" role="group" aria-label="Group by">
          {(['operator', 'machine'] as const).map((k) => (
            <Button key={k} variant={kind === k ? 'primary' : 'secondary'} aria-pressed={kind === k} onClick={() => setKind(k)}>
              {k === 'operator' ? 'Operators' : 'Machines'}
            </Button>
          ))}
        </div>
      }
    >
      <DataState
        res={res}
        isEmpty={(c) => !c || c.metrics.length === 0}
        errorTitle="Couldn't load clusters."
        empty={{ icon: Network, title: 'No weekly metrics yet.', hint: 'Clusters appear after the first weekly run (POST /analytics/cluster).' }}
        className="col-span-12"
      >
        {(c) => <ClusterView c={c} kind={kind} />}
      </DataState>
    </ManagerShell>
  );
}

function ClusterView({ c, kind }: { c: Clusters; kind: Kind }) {
  const set = c.pca[kind];
  const rows = c.metrics.filter((m) => m.entity_type === kind).sort((a, b) => (a.rank_in_site ?? 99) - (b.rank_in_site ?? 99));
  const clusters = useMemo(() => {
    const seen = new Map<number, string>();
    for (const p of set.points) seen.set(p.cluster_id, p.cluster_label);
    return [...seen.entries()].sort((a, b) => a[0] - b[0]);
  }, [set.points]);
  const latest = set.points.filter((p) => p.week_start === c.week_start);
  const history = set.points.filter((p) => p.week_start !== c.week_start);
  const outliers = rows.filter((r) => r.is_outlier);
  const [ev1, ev2] = set.explained_variance;

  return (
    <>
      <section className="col-span-8 flex flex-col gap-3 rounded-md border bg-surface p-4" aria-label="Behaviour map">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-office-h2">Who works alike</h2>
          <p className="text-office-small text-ink-2">
            Each mark is one {kind} in one week; the large marks are the week of {c.week_start}. Axes are the two main patterns (PCA,{' '}
            <span className="reading">{Math.round((ev1 + ev2) * 100)}%</span> of the variation).
          </p>
        </div>
        <ul className="flex flex-wrap gap-4 text-office-body" aria-label="Legend">
          {clusters.map(([id, label]) => (
            <li key={id} className="flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
                <path d={SHAPE_SVG[CLUSTER_SHAPE[id % 4]!]} fill={CLUSTER_COLOR[id % 4]} />
              </svg>
              {label}
            </li>
          ))}
        </ul>
        <div className="h-[420px]">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
              <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} />
              <XAxis type="number" dataKey="pc1" name="Pattern 1" stroke="var(--text-2)" tick={{ fontSize: 13 }} label={{ value: `Pattern 1 · more fuel and idle →`, position: 'insideBottom', offset: -12, fill: 'var(--text-2)', fontSize: 13 }} />
              <YAxis type="number" dataKey="pc2" name="Pattern 2" stroke="var(--text-2)" tick={{ fontSize: 13 }} label={{ value: 'Pattern 2 · more output →', angle: -90, position: 'insideLeft', fill: 'var(--text-2)', fontSize: 13 }} />
              <Tooltip
                cursor={{ strokeDasharray: '3 3' }}
                content={({ payload }) => {
                  const p = payload?.[0]?.payload as PcaPoint | undefined;
                  if (!p) return null;
                  return (
                    <div className="rounded-md border bg-surface px-3 py-2 text-office-small shadow-float">
                      <p className="text-office-h3">{c.names[p.entity_id] ?? p.entity_id}</p>
                      <p className="reading">Week of {p.week_start}</p>
                      <p>{p.cluster_label}</p>
                    </div>
                  );
                }}
              />
              {clusters.map(([id]) => (
                <Scatter
                  key={`h${id}`}
                  data={history.filter((p) => p.cluster_id === id)}
                  fill={CLUSTER_COLOR[id % 4]}
                  shape={mark(id, 10, false)}
                  isAnimationActive={false}
                />
              ))}
              {clusters.map(([id]) => (
                <Scatter
                  key={`l${id}`}
                  data={latest.filter((p) => p.cluster_id === id)}
                  fill={CLUSTER_COLOR[id % 4]}
                  shape={mark(id, 18, true)}
                  isAnimationActive={false}
                >
                  <LabelList dataKey="entity_id" position="right" offset={10} fill="var(--text)" fontSize={12} fontFamily="var(--font-mono)" />
                </Scatter>
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="col-span-4 flex flex-col gap-3" aria-label="Outliers to verify">
        <h2 className="text-office-h2">Check these</h2>
        <p className="text-office-small text-ink-2">Unusual weeks flagged by the model. They are for you to verify, not verdicts.</p>
        {outliers.length === 0 && <p className="rounded-md border border-dashed p-4 text-office-body text-ink-2">No outliers this week.</p>}
        <ul className="flex flex-col gap-2">
          {outliers.map((o) => (
            <Outlier key={o.id} o={o} name={c.names[o.entity_id] ?? o.entity_id} />
          ))}
        </ul>
      </section>

      <section className="col-span-12" aria-label="Efficiency ranking">
        <h2 className="mb-3 text-office-h2">Efficiency ranking · week of {c.week_start}</h2>
        <table className="w-full rounded-md border bg-surface text-office-body">
          <thead className="bg-raised text-left text-office-small text-ink-2">
            <tr>
              <th className="px-3 py-2 font-normal">Rank</th>
              <th className="px-3 py-2 font-normal">{kind === 'operator' ? 'Operator' : 'Machine'}</th>
              <th className="px-3 py-2 font-normal">Group</th>
              <th className="px-3 py-2 text-right font-normal">Efficiency index</th>
              <th className="px-3 py-2 text-right font-normal">Idle %</th>
              <th className="px-3 py-2 text-right font-normal">Fuel L / productive h</th>
              <th className="px-3 py-2 text-right font-normal">Output / h</th>
              <th className="px-3 py-2 text-right font-normal">Time vs typical</th>
              <th className="px-3 py-2 text-right font-normal">Safety events</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t">
                <td className="reading px-3 py-2">{r.rank_in_site}</td>
                <td className="px-3 py-2">
                  {c.names[r.entity_id] ?? r.entity_id} <span className="reading text-office-small text-ink-2">{r.entity_id}</span>
                </td>
                <td className="px-3 py-2">
                  <span className="flex items-center gap-2">
                    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
                      <path d={SHAPE_SVG[CLUSTER_SHAPE[(r.cluster_id ?? 0) % 4]!]} fill={CLUSTER_COLOR[(r.cluster_id ?? 0) % 4]} />
                    </svg>
                    {r.cluster_label}
                  </span>
                </td>
                <td className={cx('reading px-3 py-2 text-right')}>{r.efficiency_index?.toFixed(2)}</td>
                <td className="reading px-3 py-2 text-right">{r.idle_pct?.toFixed(1)}</td>
                <td className="reading px-3 py-2 text-right">{r.fuel_per_productive_hour?.toFixed(1)}</td>
                <td className="reading px-3 py-2 text-right">{r.productivity_per_hour?.toFixed(0)}</td>
                <td className="reading px-3 py-2 text-right">{r.time_ratio == null ? '—' : `${r.time_ratio.toFixed(2)}×`}</td>
                <td className="reading px-3 py-2 text-right">{r.safety_event_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Outlier({ o, name }: { o: FleetMetricRow; name: string }) {
  return (
    <li className="flex flex-col gap-2 rounded-md border bg-surface p-3">
      <p className="text-office-h3">
        {name} <span className="reading text-office-small text-ink-2">{o.entity_id}</span>
      </p>
      <p className="text-office-body">{o.outlier_reason}</p>
      {o.verified_at ? (
        <StatusBadge status="ok" label="Verified" />
      ) : (
        <Button variant="secondary" icon={SearchCheck} onClick={() => void verifyOutlier(o.id)} className="self-start">
          Mark as verified
        </Button>
      )}
    </li>
  );
}

