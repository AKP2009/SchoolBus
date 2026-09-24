import { useEffect, useState, type ReactNode } from 'react';
import { USE_MOCKS } from '@/data/config';
import { useOpenAlerts, useWorld } from '@/data/hooks';
import { useLive, useLiveStream } from '@/data/live';
import { OfficeLayout } from '@/layouts/OfficeLayout';

/** Data time now: the mock replay clock, else the wall clock. */
export function useOfficeNow(): number {
  const clock = useLive((s) => s.clock);
  const [wall, setWall] = useState(Date.now());
  useEffect(() => {
    if (USE_MOCKS) return;
    const id = window.setInterval(() => setWall(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  return USE_MOCKS && clock ? clock : wall;
}

export function useSiteId(): string | undefined {
  return useWorld().data?.site.site_id;
}

export function ManagerShell({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const world = useWorld();
  const siteId = world.data?.site.site_id;
  // Mock mode: the recorded stream drives the "live" alert feed; real mode uses Supabase Realtime.
  useLiveStream(USE_MOCKS ? (world.data?.machine.machine_id ?? null) : null);
  const alerts = useOpenAlerts(siteId);
  const open = alerts.data ?? [];
  return (
    <OfficeLayout
      title={title}
      actions={actions}
      sites={(world.data?.sites ?? []).map((s) => ({ id: s.site_id, name: `${s.site_id} · ${s.name}` }))}
      siteId={siteId}
      alertCount={{ open: open.length, critical: open.filter((a) => a.severity === 'critical' || a.severity === 'emergency').length }}
      userName={world.data?.manager.name ?? 'Site manager'}
    >
      {children}
    </OfficeLayout>
  );
}
