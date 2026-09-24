import { useEffect } from 'react';
import { create } from 'zustand';
import type {
  CameraSector,
  StreamAlert,
  StreamFatigue,
  StreamHealth,
  StreamMessage,
  StreamSafety,
  Telemetry,
  TelemetryStream,
} from '@/types/domain';
import { USE_MOCKS, WS_URL, urlNumber } from './config';
import { mock } from './mocks';

/**
 * Live machine state from `ws /stream/{machine_id}` (docs/api_contract.md). In mock mode a
 * MockSocket replays web/src/mocks/telemetry_stream.json on a data clock that the demo panel can
 * play, pause, speed up and seek; in real mode a WebSocket feeds the same reducer.
 */

export interface SectorState {
  distance_m: number | null;
  approaching: boolean;
  /** Data time of the reading (ms). */
  at: number;
}
export interface LiveAlert extends StreamAlert {
  /** Data time the latest message for this alert arrived (ms). */
  at: number;
}
export interface FatiguePoint {
  at: number;
  score: number;
  level: StreamFatigue['fatigue_level'];
}

interface LiveData {
  telemetry: Telemetry | null;
  /** Last 60 telemetry rows, oldest first (trend arrows, sparklines). */
  recent: Telemetry[];
  health: StreamHealth | null;
  fatigue: StreamFatigue | null;
  fatigueSeries: FatiguePoint[];
  sectors: Partial<Record<CameraSector, SectorState>>;
  alerts: Record<number, LiveAlert>;
  safetyLog: Array<StreamSafety & { at: number }>;
}

export interface LiveState extends LiveData {
  machineId: string | null;
  source: 'mock' | 'ws';
  connected: boolean;
  /** Data time now (ms): the replay clock in mock mode, the last telemetry ts on a real stream. */
  clock: number;
  // mock replay
  fromMs: number;
  endS: number;
  t: number;
  playing: boolean;
  speed: number;
}

const EMPTY: LiveData = {
  telemetry: null,
  recent: [],
  health: null,
  fatigue: null,
  fatigueSeries: [],
  sectors: {},
  alerts: {},
  safetyLog: [],
};

export const SECTOR_HOLD_MS = 45_000;

export const useLive = create<LiveState>(() => ({
  ...EMPTY,
  machineId: null,
  source: USE_MOCKS ? 'mock' : 'ws',
  connected: false,
  clock: 0,
  fromMs: 0,
  endS: 0,
  t: 0,
  playing: false,
  speed: 10,
}));

/** Pure reducer: one stream message at data time `at` (ms). */
export function applyMessage(d: LiveData, msg: StreamMessage, at: number): LiveData {
  switch (msg.kind) {
    case 'telemetry': {
      const recent = [...d.recent, msg.data].slice(-60);
      return { ...d, telemetry: msg.data, recent };
    }
    case 'health':
      return { ...d, health: msg.data };
    case 'fatigue':
      return {
        ...d,
        fatigue: msg.data,
        fatigueSeries: [...d.fatigueSeries, { at, score: msg.data.fatigue_score, level: msg.data.fatigue_level }].slice(-240),
      };
    case 'safety': {
      const s = msg.data;
      const sectors =
        s.sector && s.sector !== 'cab' && s.distance_m != null
          ? { ...d.sectors, [s.sector]: { distance_m: s.distance_m, approaching: !!s.approaching, at } }
          : d.sectors;
      return { ...d, sectors, safetyLog: [...d.safetyLog, { ...s, at }].slice(-50) };
    }
    case 'alert':
      return { ...d, alerts: { ...d.alerts, [msg.data.id]: { ...msg.data, at } } };
  }
}

// ---------------------------------------------------------------------------------------------
// Mock socket: the recorded stream on a data clock
// ---------------------------------------------------------------------------------------------
type Timed = StreamMessage & { at_s: number };
let messages: Timed[] = [];
let cursor = 0;
let timer: number | null = null;
const TICK_MS = 200;

function msgTime(m: Timed, fromMs: number): number {
  return m.kind === 'telemetry' ? Date.parse(m.data.ts) : fromMs + m.at_s * 1000;
}

function foldTo(t: number) {
  const { fromMs } = useLive.getState();
  let d: LiveData = EMPTY;
  let i = 0;
  for (; i < messages.length && messages[i]!.at_s <= t; i++) d = applyMessage(d, messages[i]!, msgTime(messages[i]!, fromMs));
  cursor = i;
  useLive.setState({ ...d, t, clock: fromMs + t * 1000 });
}

function advance(dtS: number) {
  const s = useLive.getState();
  const t = Math.min(s.endS, s.t + dtS);
  let d: LiveData = s;
  let changed = false;
  while (cursor < messages.length && messages[cursor]!.at_s <= t) {
    d = applyMessage(d, messages[cursor]!, msgTime(messages[cursor]!, s.fromMs));
    cursor++;
    changed = true;
  }
  useLive.setState({
    ...(changed ? d : {}),
    t,
    clock: s.fromMs + t * 1000,
    playing: t < s.endS && s.playing,
  });
}

function startTimer() {
  if (timer != null) return;
  timer = window.setInterval(() => {
    const s = useLive.getState();
    if (s.playing && s.speed > 0) advance((TICK_MS / 1000) * s.speed);
  }, TICK_MS);
}

async function openMock(machineId: string) {
  const stream: TelemetryStream = await mock('telemetry_stream');
  if (stream.machine_id !== machineId) {
    // Only the demo machine has a recorded stream; others stay quiet (no live data).
    messages = [];
    useLive.setState({ ...EMPTY, machineId, connected: true, fromMs: Date.parse(stream.to), endS: 0, t: 0, playing: false });
    return;
  }
  messages = [...stream.messages];
  const endS = (Date.parse(stream.to) - Date.parse(stream.from)) / 1000;
  const speed = urlNumber('speed') ?? 10;
  useLive.setState({ machineId, connected: true, fromMs: Date.parse(stream.from), endS, speed, playing: speed > 0 });
  foldTo(Math.max(0, Math.min(endS, urlNumber('t') ?? 60)));
  startTimer();
}

/** Alert ids that the mock stream carries (their state comes from the replay clock, not the snapshot). */
export function streamAlertIds(): Set<number> {
  return new Set(messages.filter((m) => m.kind === 'alert').map((m) => (m.data as StreamAlert).id));
}

/** Demo panel controls for the mock replay (no-ops on a real stream: use POST /replay/*). */
export const mockReplay = {
  play: () => useLive.setState((s) => ({ playing: s.t < s.endS })),
  pause: () => useLive.setState({ playing: false }),
  setSpeed: (speed: number) => useLive.setState({ speed }),
  seek: (t: number) => foldTo(Math.max(0, Math.min(useLive.getState().endS, t))),
  /** First message matching `pred`, in data seconds (to jump to a recorded event). */
  find: (pred: (m: Timed) => boolean): number | null => messages.find(pred)?.at_s ?? null,
  /** Splice client-side messages in at `offsetS` data seconds from now (simulated scenarios). */
  inject: (items: Array<StreamMessage & { offsetS: number }>) => {
    const now = useLive.getState().t;
    const add = items.map(({ offsetS, ...m }) => ({ ...m, at_s: now + offsetS }) as Timed);
    messages = [...messages.slice(0, cursor), ...[...messages.slice(cursor), ...add].sort((a, b) => a.at_s - b.at_s)];
    const s = useLive.getState();
    if (!s.playing) useLive.setState({ playing: true, speed: s.speed || 10 });
  },
};

// ---------------------------------------------------------------------------------------------
// Real WebSocket
// ---------------------------------------------------------------------------------------------
let ws: WebSocket | null = null;
let ping: number | null = null;
let retry: number | null = null;

function openWs(machineId: string) {
  closeWs();
  useLive.setState({ ...EMPTY, machineId, connected: false, source: 'ws' });
  const sock = new WebSocket(`${WS_URL}/stream/${encodeURIComponent(machineId)}`);
  ws = sock;
  sock.onopen = () => {
    useLive.setState({ connected: true });
    ping = window.setInterval(() => sock.readyState === WebSocket.OPEN && sock.send(JSON.stringify({ kind: 'ping' })), 20_000);
  };
  sock.onmessage = (ev) => {
    for (const line of String(ev.data).split('\n')) {
      if (!line.trim()) continue;
      const msg = JSON.parse(line) as StreamMessage;
      const s = useLive.getState();
      const at = msg.kind === 'telemetry' ? Date.parse(msg.data.ts) : s.clock || Date.now();
      useLive.setState({ ...applyMessage(s, msg, at), clock: Math.max(s.clock, at) });
    }
  };
  sock.onclose = () => {
    useLive.setState({ connected: false });
    if (ping != null) window.clearInterval(ping);
    if (ws === sock) retry = window.setTimeout(() => openWs(machineId), 3000);
  };
}

function closeWs() {
  if (retry != null) window.clearTimeout(retry);
  if (ping != null) window.clearInterval(ping);
  const sock = ws;
  ws = null;
  sock?.close();
}

let users = 0;
let current: string | null = null;

/** Keeps the stream for `machineId` open while mounted (both layouts mount it). */
export function useLiveStream(machineId: string | null): void {
  useEffect(() => {
    if (!machineId) return;
    users++;
    if (current !== machineId) {
      current = machineId;
      if (USE_MOCKS) void openMock(machineId);
      else openWs(machineId);
    }
    return () => {
      users--;
      if (users === 0 && !USE_MOCKS) {
        closeWs();
        current = null;
      }
    };
  }, [machineId]);
}

/** Sector readings still fresh at the current data time, in the SafetyPanel shape. */
export function freshSectors(s: Pick<LiveState, 'sectors' | 'clock'>) {
  const out: Record<'front' | 'rear' | 'left' | 'right', { distance_m: number | null; approaching?: boolean }> = {
    front: { distance_m: null },
    rear: { distance_m: null },
    left: { distance_m: null },
    right: { distance_m: null },
  };
  for (const k of ['front', 'rear', 'left', 'right'] as const) {
    const r = s.sectors[k];
    if (r && s.clock - r.at <= SECTOR_HOLD_MS && s.clock >= r.at) out[k] = { distance_m: r.distance_m, approaching: r.approaching };
  }
  return out;
}
