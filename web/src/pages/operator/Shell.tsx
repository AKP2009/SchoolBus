import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useShallow } from 'zustand/react/shallow';
import { Skeleton } from '@/components/EmptyState';
import { SafetyPanel } from '@/components/SafetyPanel';
import { USE_MOCKS, urlString } from '@/data/config';
import { signInOperator, useCabAlerts } from '@/data/hooks';
import { freshSectors, useDataNow, useLive, useLiveStream } from '@/data/live';
import * as api from '@/data/api';
import { CabLayout } from '@/layouts/CabLayout';
import { orderTakeovers } from '@/components/AlertTakeover';
import { useAlertSounds } from '@/lib/tones';
import { useAlerts } from '@/stores/alerts';
import { useSession, type CabSession } from '@/stores/session';
import { useT } from '@/i18n';

/** The cab session, or null while `?as=` signs in (mock screenshots). */
export function useCab(): CabSession | null {
  return useSession((s) => s.cab);
}

/** Data time now: the replay clock in mock mode; live, the stream / running replay (data/live.ts). */
export function useNow(): number {
  return useDataNow();
}

/** Safety zone: top-down sectors, fatigue, seatbelt, tilt from the live stream. */
export function LiveSafetyPanel() {
  const t = useT();
  const [sectors, clock, fatigue, telemetry] = useLive(useShallow((s) => [s.sectors, s.clock, s.fatigue, s.telemetry] as const));
  if (!telemetry) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true" aria-label={t('cab.waitingMachineAria')}>
        <Skeleton className="mx-auto aspect-square w-full max-w-[400px]" />
        <p className="text-cab-small text-ink-2">{t('cab.waitingMachine')}</p>
      </div>
    );
  }
  return (
    <SafetyPanel
      sectors={freshSectors({ sectors, clock })}
      fatigue={fatigue?.fatigue_level ?? 'low'}
      seatbeltFastened={telemetry.seatbelt_fastened !== false}
      pitchDeg={telemetry.pitch_deg ?? 0}
      rollDeg={telemetry.roll_deg ?? 0}
    />
  );
}

export interface OperatorShellProps {
  children: ReactNode;
  /** Replaces the safety zone (defaults to the live SafetyPanel). */
  safety?: ReactNode;
}

/** Wraps every signed-in cab screen: session guard, live stream, alerts, sounds, SOS, voice. */
export function OperatorShell({ children, safety }: OperatorShellProps) {
  const t = useT();
  const cab = useCab();
  const location = useLocation();
  const as = urlString('as');
  useLiveStream(cab?.machineId ?? null);
  const telemetry = useLive((s) => s.telemetry);
  const now = useNow();
  const { takeovers, banner, acknowledge, dismiss } = useCabAlerts(cab?.machineType);
  const push = useAlerts((s) => s.push);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!cab && as) void signInOperator(`${as.toLowerCase()}@demo.site`, 'demo1234').then(() => useSession.getState().markHandoverSeen());
  }, [cab, as]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(t);
  }, [toast]);

  useAlertSounds(orderTakeovers(takeovers)[0] ?? null, banner);

  if (!cab) {
    if (as) return null;
    return <Navigate to="/operator/login" replace state={{ from: location.pathname }} />;
  }

  const sos = () => {
    push({
      id: -Date.now(),
      ts: new Date().toISOString(),
      machine_id: cab.machineId,
      title: t('sos.title'),
      message: t('sos.message'),
      recommended_action: t('sos.action'),
      severity: 'emergency',
      stage: 'escalated',
    });
    if (!USE_MOCKS)
      void api
        .postEvent({ type: 'sos', machine_id: cab.machineId, operator_id: cab.operatorId, ts: new Date().toISOString(), severity: 'emergency', details: { shift_id: cab.shiftId } })
        .catch(() => undefined);
  };

  return (
    <CabLayout
      status={{
        machineId: cab.machineId,
        shiftMinutes: (now - Date.parse(cab.shiftStart)) / 60000,
        fuelPct: telemetry?.fuel_level_pct ?? cab.fuelStartPct,
      }}
      primary={children}
      safety={safety ?? <LiveSafetyPanel />}
      takeovers={takeovers}
      onAcknowledge={acknowledge}
      banner={banner}
      onDismissBanner={dismiss}
      toast={toast}
      onVoice={() => setToast(t('cab.voiceNotConnected'))}
      onSos={sos}
    />
  );
}
