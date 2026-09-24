import { useMemo } from 'react';
import { Wrench } from 'lucide-react';
import { useShallow } from 'zustand/react/shallow';
import { DigitalTwin, type Subsystem as TwinSubsystem, type TwinSignal } from '@/components/DigitalTwin';
import { DataState } from '@/components/DataState';
import { Skeleton } from '@/components/EmptyState';
import { Gauge, type Trend } from '@/components/Gauge';
import { StatusBadge } from '@/components/StatusBadge';
import { useMachineHealth, useMachineSignals } from '@/data/hooks';
import { useLive } from '@/data/live';
import { COMPONENT_WORD } from '@/lib/format';
import type { Status } from '@/lib/status';
import type { HealthScores, MachineHealth, MachineType, Telemetry } from '@/types/domain';
import { OperatorShell, useCab } from './Shell';

type Signal = keyof Telemetry;

// Which telemetry signals sit behind each twin region (models.md §11).
const REGION_SIGNALS: Record<TwinSubsystem, Array<{ key: Signal; label: string; unit: string }>> = {
  engine: [
    { key: 'engine_rpm', label: 'Engine speed', unit: 'rpm' },
    { key: 'oil_pressure_kpa', label: 'Oil pressure', unit: 'kPa' },
  ],
  cooling: [
    { key: 'coolant_temp_c', label: 'Coolant temperature', unit: '°C' },
    { key: 'engine_oil_temp_c', label: 'Engine oil temperature', unit: '°C' },
  ],
  hydraulics: [
    { key: 'hydraulic_oil_temp_c', label: 'Hydraulic oil temperature', unit: '°C' },
    { key: 'hydraulic_pressure_bar', label: 'Hydraulic pressure', unit: 'bar' },
  ],
  electrical: [{ key: 'battery_voltage', label: 'Battery voltage', unit: 'V' }],
  undercarriage: [{ key: 'vibration_rms_g', label: 'Vibration', unit: 'g' }],
};

/** Rule-engine limits (backend/app/alerts/thresholds.yaml), not the normal band, decide the badge. */
function above(v: number | null | undefined, warn: number, crit: number): Status | undefined {
  if (v == null) return undefined;
  return v > crit ? 'critical' : v > warn ? 'warning' : 'ok';
}

function trendOf(recent: Telemetry[], key: Signal, eps: number): Trend | undefined {
  const pts = recent.slice(-6).map((r) => r[key]).filter((v): v is number => typeof v === 'number');
  if (pts.length < 3) return undefined;
  const d = pts[pts.length - 1]! - pts[0]!;
  return d > eps ? 'rising' : d < -eps ? 'falling' : 'steady';
}

export function toScores(h: { subsystems: MachineHealth['subsystems'] } | null | undefined): HealthScores {
  return {
    engine_score: h?.subsystems.engine ?? null,
    cooling_score: h?.subsystems.cooling ?? null,
    hydraulics_score: h?.subsystems.hydraulics ?? null,
    electrical_score: h?.subsystems.electrical ?? null,
    undercarriage_score: h?.subsystems.undercarriage ?? null,
  };
}

export function OperatorMachine() {
  const cab = useCab();
  const [telemetry, recent, liveHealth] = useLive(useShallow((s) => [s.telemetry, s.recent, s.health] as const));
  const health = useMachineHealth(cab?.machineId);
  const history = useMachineSignals(cab?.machineId);

  const signals = useMemo(() => {
    const out: Partial<Record<TwinSubsystem, TwinSignal[]>> = {};
    for (const [region, list] of Object.entries(REGION_SIGNALS) as Array<[TwinSubsystem, (typeof REGION_SIGNALS)[TwinSubsystem]]>) {
      out[region] = list.map(({ key, label, unit }) => {
        const live = recent.map((r) => r[key]).filter((v): v is number => typeof v === 'number');
        const hist = (history.data?.series[key] ?? []).filter((v): v is number => typeof v === 'number');
        return { label, unit, points: live.length >= 5 ? live : hist };
      });
    }
    return out;
  }, [recent, history.data]);

  const t = telemetry;
  const failure = health.data?.failure_probability;

  return (
    <OperatorShell>
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-cab-h1">Machine</h1>
        {failure != null && failure >= 0.3 ? (
          <StatusBadge
            status={failure >= 0.6 ? 'critical' : 'warning'}
            label={`${COMPONENT_WORD[health.data?.likely_component ?? 'other']}: tell maintenance at the end of the task`}
          />
        ) : (
          health.status === 'ready' && <StatusBadge status="ok" label="No repair expected in the next 48 h" />
        )}
      </div>

      <DataState
        res={{ ...health, data: liveHealth ?? health.data }}
        isEmpty={(h) => !h}
        errorTitle="Couldn't load machine health."
        empty={{ icon: Wrench, title: 'No health reading yet.', hint: 'Health appears a minute after the machine starts reporting.' }}
        skeleton={<Skeleton className="h-[300px]" />}
      >
        {(h) => <DigitalTwin scores={toScores(h)} signals={signals} machineType={cab?.machineType as MachineType | undefined} className="min-h-[352px]" />}
      </DataState>

      {t ? (
        <div className="grid grid-cols-3 gap-x-8 gap-y-6">
          <Gauge label="Coolant" value={t.coolant_temp_c} unit="°C" min={40} max={120} normal={[75, 100]} status={above(t.coolant_temp_c, 100, 105)} trend={trendOf(recent, 'coolant_temp_c', 1)} />
          <Gauge label="Hydraulic oil" value={t.hydraulic_oil_temp_c} unit="°C" min={20} max={110} normal={[40, 85]} status={above(t.hydraulic_oil_temp_c, 90, 95)} trend={trendOf(recent, 'hydraulic_oil_temp_c', 1)} />
          <Gauge
            label="Oil pressure"
            value={t.oil_pressure_kpa}
            unit="kPa"
            min={0}
            max={600}
            normal={[250, 500]}
            status={t.oil_pressure_kpa == null ? undefined : t.oil_pressure_kpa < 100 && (t.engine_rpm ?? 0) > 1200 ? 'critical' : 'ok'}
            trend={trendOf(recent, 'oil_pressure_kpa', 15)}
          />
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-8" aria-busy="true" aria-label="Waiting for readings">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}
    </OperatorShell>
  );
}
