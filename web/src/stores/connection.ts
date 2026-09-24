import { create } from 'zustand';

interface ConnectionState {
  /** navigator.onLine and not forced offline. */
  online: boolean;
  /** The browser's own view. */
  browserOnline: boolean;
  /** Demo panel "offline" scenario / ?state=offline: behave as offline with the network up. */
  forcedOffline: boolean;
  /** Offline writes waiting in the IndexedDB queue (src/lib/offlineQueue.ts). */
  queued: number;
  setQueued: (n: number) => void;
  setForcedOffline: (on: boolean) => void;
}

const initialOnline = typeof navigator === 'undefined' ? true : navigator.onLine;
const initialForced =
  typeof window !== 'undefined' &&
  import.meta.env.VITE_USE_MOCKS !== 'false' &&
  new URLSearchParams(window.location.search).get('state') === 'offline';

export const useConnection = create<ConnectionState>((set) => ({
  online: initialOnline && !initialForced,
  browserOnline: initialOnline,
  forcedOffline: initialForced,
  queued: 0,
  setQueued: (queued) => set({ queued }),
  setForcedOffline: (forcedOffline) => set((s) => ({ forcedOffline, online: s.browserOnline && !forcedOffline })),
}));

if (typeof window !== 'undefined') {
  window.addEventListener('online', () =>
    useConnection.setState((s) => ({ browserOnline: true, online: !s.forcedOffline })),
  );
  window.addEventListener('offline', () => useConnection.setState({ browserOnline: false, online: false }));
}
