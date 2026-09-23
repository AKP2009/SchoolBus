import { create } from 'zustand';
import type { Alert } from '@/types/domain';

interface AlertState {
  /** Open critical/emergency alerts; AlertTakeover shows one at a time. */
  takeovers: Alert[];
  /** Open warning/info alerts shown as banners/toasts. */
  banners: Alert[];
  push: (alert: Alert) => void;
  acknowledge: (id: number) => void;
  dismiss: (id: number) => void;
}

export const useAlerts = create<AlertState>((set) => ({
  takeovers: [],
  banners: [],
  push: (alert) =>
    set((s) => {
      const bucket = alert.severity === 'critical' || alert.severity === 'emergency' ? 'takeovers' : 'banners';
      if (s[bucket].some((a) => a.id === alert.id)) return s;
      return { [bucket]: [...s[bucket], alert] } as Partial<AlertState>;
    }),
  acknowledge: (id) => set((s) => ({ takeovers: s.takeovers.filter((a) => a.id !== id) })),
  dismiss: (id) => set((s) => ({ banners: s.banners.filter((a) => a.id !== id) })),
}));
