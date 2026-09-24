import { create } from 'zustand';
import type { Alert, AlertStage } from '@/types/domain';

/**
 * Cab-side alert bookkeeping. Stream alerts live in `useLive`; this store holds what the operator
 * did with them (acknowledged / dismissed at which stage) plus local alerts such as SOS.
 * A graded alert that moves to a later stage (warn → derate → recommend shutdown) takes over again.
 */
interface AlertState {
  local: Alert[];
  acked: Record<number, AlertStage>;
  dismissed: Record<number, AlertStage>;
  push: (alert: Alert) => void;
  acknowledge: (id: number, stage: AlertStage) => void;
  dismiss: (id: number, stage: AlertStage) => void;
  clearLocal: (id: number) => void;
  reset: () => void;
}

/** Mock QA: ?ack=8,5 starts with those alerts acknowledged (screenshots of later takeovers). */
function initialAcks(): Record<number, AlertStage> {
  if (typeof window === 'undefined' || import.meta.env.VITE_USE_MOCKS === 'false') return {};
  const ids = new URLSearchParams(window.location.search).get('ack');
  return Object.fromEntries((ids ?? '').split(',').filter(Boolean).map((id) => [Number(id), 'escalated' as AlertStage]));
}

export const useAlerts = create<AlertState>((set) => ({
  local: [],
  acked: initialAcks(),
  dismissed: {},
  push: (alert) => set((s) => (s.local.some((a) => a.id === alert.id) ? s : { local: [...s.local, alert] })),
  acknowledge: (id, stage) =>
    set((s) => ({ acked: { ...s.acked, [id]: stage }, local: s.local.filter((a) => a.id !== id || a.severity === 'emergency') })),
  dismiss: (id, stage) => set((s) => ({ dismissed: { ...s.dismissed, [id]: stage } })),
  clearLocal: (id) => set((s) => ({ local: s.local.filter((a) => a.id !== id) })),
  reset: () => set({ acked: {}, dismissed: {} }),
}));

export const STAGE_RANK: Record<AlertStage, number> = { warn: 0, derate: 1, recommend_shutdown: 2, escalated: 3, resolved: 4 };

/** Manager-side acknowledge / resolve made in this session (optimistic over the feed). */
interface OverlayState {
  acked: Record<number, string>;
  resolved: Record<number, string>;
  ack: (id: number) => void;
  resolve: (id: number) => void;
}
export const useAlertOverlay = create<OverlayState>((set) => ({
  acked: {},
  resolved: {},
  ack: (id) => set((s) => ({ acked: { ...s.acked, [id]: new Date().toISOString() } })),
  resolve: (id) => set((s) => ({ resolved: { ...s.resolved, [id]: new Date().toISOString() } })),
}));
