import { useEffect, useState } from 'react';
import { create } from 'zustand';
import type {
  CameraSector,
  StreamAlert,
  StreamFatigue,
  StreamHealth,
  StreamMessage,
  StreamSafety,
  Telemetry,
  ReplayStatus,
  TelemetryStream,
} from '@/types/domain';
import { replayStatus } from './api';
import { accessToken, refreshToken } from './auth';
import { DATA_NOW, USE_MOCKS, WS_URL, urlNumber } from './config';
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
// Real WebSocket: ws /stream/{machine_id}?token=<Supabase access token>
// ---------------------------------------------------------------------------------------------
let ws: WebSocket | null = null;
let ping: number | null = null;
let retry: number | null = null;
let attempts = 0;

/** Why the stream is down, for the status bar (null while connected or connecting normally). */
export type StreamProblem = 'no_session' | 'forbidden' | null;
export const useStreamProblem = create<{ problem: StreamProblem }>(() => ({ problem: null }));

function scheduleRetry(machineId: string, delayMs?: number) {
  if (retry != null) window.clearTimeout(retry);
  // 1 s, 2 s, 4 s … capped at 30 s, with jitter so a site's tablets don't reconnect in lockstep.
  const wait = delayMs ?? Math.min(30_000, 1000 * 2 ** attempts) * (0.75 + Math.random() * 0.5);
  attempts++;
  retry = window.setTimeout(() => void openWs(machineId), wait);
}

async function openWs(machineId: string) {
  if (current !== machineId) return;
  if (!navigator.onLine) return; // the 'online' listener below reconnects
  const token = await accessToken();
  if (current !== machineId) return;
  if (!token) {
    useStreamProblem.setState({ problem: 'no_session' });
    scheduleRetry(machineId);
    return;
  }
  const sock = new WebSocket(`${WS_URL}/stream/${encodeURIComponent(machineId)}?token=${encodeURIComponent(token)}`);
  ws = sock;
  sock.onopen = () => {
    attempts = 0;
    useStreamProblem.setState({ problem: null });
    useLive.setState({ connected: true });
    if (ping != null) window.clearInterval(ping);
    ping = window.setInterval(() => sock.readyState === WebSocket.OPEN && sock.send(JSON.stringify({ kind: 'ping' })), 20_000);
  };
  sock.onmessage = (ev) => {
    for (const line of String(ev.data).split('\n')) {
      if (!line.trim()) continue;
      let msg: StreamMessage;
      try {
        msg = JSON.parse(line) as StreamMessage;
      } catch {
        continue;
      }
      const s = useLive.getState();
      const at = msg.kind === 'telemetry' ? Date.parse(msg.data.ts) : s.clock || Date.now();
      useLive.setState({ ...applyMessage(s, msg, at), clock: Math.max(s.clock, at) });
    }
  };
  sock.onclose = (ev) => {
    if (ping != null) window.clearInterval(ping);
    ping = null;
    if (ws !== sock) return; // replaced or closed on purpose
    ws = null;
    useLive.setState({ connected: false });
    if (ev.code === 4403) {
      useStreamProblem.setState({ problem: 'forbidden' }); // not allowed on this machine: don't hammer
      return;
    }
    if (ev.code === 4401) {
      // Token expired or rejected: refresh once, then reconnect straight away.
      void refreshToken().then((t) => (t ? scheduleRetry(machineId, 0) : (useStreamProblem.setState({ problem: 'no_session' }), scheduleRetry(machineId))));
      return;
    }
    scheduleRetry(machineId);
  };
}

function closeWs() {
  if (retry != null) window.clearTimeout(retry);
  if (ping != null) window.clearInterval(ping);
  retry = ping = null;
  attempts = 0;
  const sock = ws;
  ws = null;
  sock?.close();
}

if (typeof window !== 'undefined' && !USE_MOCKS) {
  window.addEventListener('online', () => {
    if (current && !ws) {
      attempts = 0;
      void openWs(current);
    }
  });
}

let users = 0;
let current: string | null = null;
let closeTimer: number | null = null;
/** Moving between cab screens unmounts one shell and mounts the next: keep the socket across that. */
const CLOSE_GRACE_MS = 3000;

/** Keeps the stream for `machineId` open while mounted (both layouts mount it). */
export function useLiveStream(machineId: string | null): void {
  useEffect(() => {
    if (!machineId) return;
    users++;
    if (closeTimer != null) {
      window.clearTimeout(closeTimer);
      closeTimer = null;
    }
    if (current !== machineId) {
      if (USE_MOCKS) {
        current = machineId;
        void openMock(machineId);
      } else {
        closeWs();
        current = machineId;
        useLive.setState({ ...EMPTY, machineId, connected: false, source: 'ws', clock: 0 });
        void openWs(machineId);
      }
    }
    return () => {
      users--;
      if (users === 0 && !USE_MOCKS) {
        closeTimer = window.setTimeout(() => {
          closeTimer = null;
          if (users > 0) return;
          current = null;
          closeWs();
        }, CLOSE_GRACE_MS);
      }
    };
  }, [machineId]);
}

// ---------------------------------------------------------------------------------------------
// Data time (live mode)
// ---------------------------------------------------------------------------------------------
interface ReplayClock {
  /** Replay data time (ms) at `at` (wall ms), advancing at `speed`; null when no replay runs. */
  ts: number | null;
  at: number;
  speed: number;
  running: boolean;
  machineIds: string[];
}
export const useReplayClock = create<ReplayClock>(() => ({ ts: null, at: 0, speed: 1, running: false, machineIds: [] }));

export function setReplayStatus(s: ReplayStatus | null): void {
  useReplayClock.setState(
    s?.running && s.replay_ts
      ? { ts: Date.parse(s.replay_ts), at: Date.now(), speed: s.speed ?? 1, running: true, machineIds: s.machine_ids }
      : { ts: null, at: Date.now(), speed: 1, running: false, machineIds: s?.machine_ids ?? [] },
  );
}

/** Data time now (ms), outside React. Mock: the replay clock. Live: stream → running replay → VITE_DATA_NOW → wall. */
export function dataNowMs(): number {
  const { clock } = useLive.getState();
  if (clock) return clock;
  if (USE_MOCKS) return Date.now();
  const r = useReplayClock.getState();
  if (r.running && r.ts != null) return r.ts + (Date.now() - r.at) * r.speed;
  return DATA_NOW ?? Date.now();
}

let pollers = 0;
let pollTimer: number | null = null;
const pollReplay = () => void replayStatus().then(setReplayStatus, () => undefined);

/** Data time now, re-rendered every `everyMs`; live mode also polls GET /replay/status while mounted. */
export function useDataNow(everyMs = 30_000): number {
  const clock = useLive((s) => s.clock);
  useReplayClock((s) => s.ts); // re-render when the replay starts, stops or seeks
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  useEffect(() => {
    if (USE_MOCKS) return;
    if (pollers++ === 0) {
      pollReplay();
      pollTimer = window.setInterval(pollReplay, 15_000);
    }
    return () => {
      if (--pollers === 0 && pollTimer != null) window.clearInterval(pollTimer);
    };
  }, []);
  return clock || dataNowMs();
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
