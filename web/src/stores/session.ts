import { create } from 'zustand';

/** The signed-in cab session. Kept in localStorage so a reload in the cab doesn't sign the operator out. */
export interface CabSession {
  operatorId: string;
  operatorName: string;
  machineId: string;
  machineType: string;
  shiftId: string;
  shiftType: 'day' | 'night';
  shiftStart: string;
  shiftEnd: string;
  siteId: string;
  fuelStartPct: number | null;
  /** The handover brief was read (it opens once per shift). */
  handoverSeen: boolean;
}

interface SessionState {
  cab: CabSession | null;
  signIn: (s: CabSession) => void;
  markHandoverSeen: () => void;
  signOut: () => void;
}

const KEY = 'cab-session';

function read(): CabSession | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as CabSession) : null;
  } catch {
    return null;
  }
}

function write(s: CabSession | null) {
  try {
    if (s) window.localStorage.setItem(KEY, JSON.stringify(s));
    else window.localStorage.removeItem(KEY);
  } catch {
    /* private mode: the session lives for this tab only */
  }
}

export const useSession = create<SessionState>((set, get) => ({
  cab: typeof window === 'undefined' ? null : read(),
  signIn: (cab) => {
    write(cab);
    set({ cab });
  },
  markHandoverSeen: () => {
    const cab = get().cab;
    if (!cab) return;
    const next = { ...cab, handoverSeen: true };
    write(next);
    set({ cab: next });
  },
  signOut: () => {
    write(null);
    set({ cab: null });
  },
}));
