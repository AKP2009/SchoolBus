import { create } from 'zustand';

interface ConnectionState {
  online: boolean;
  /** Offline writes waiting in the IndexedDB queue (src/lib/offlineQueue.ts). */
  queued: number;
  setQueued: (n: number) => void;
}

export const useConnection = create<ConnectionState>((set) => ({
  online: typeof navigator === 'undefined' ? true : navigator.onLine,
  queued: 0,
  setQueued: (queued) => set({ queued }),
}));

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => useConnection.setState({ online: true }));
  window.addEventListener('offline', () => useConnection.setState({ online: false }));
}
