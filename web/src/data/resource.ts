import { useCallback, useEffect, useRef, useState } from 'react';
import { forcedState } from './config';

export type ResourceStatus = 'loading' | 'ready' | 'error';

export interface Resource<T> {
  status: ResourceStatus;
  data: T | undefined;
  /** What went wrong, in words for the error state. */
  error: string | null;
  /** When the data was last loaded (ms); shown as "saved at" when offline. */
  loadedAt: number | null;
  /** Data came from the cache because a reload failed (offline). */
  stale: boolean;
  reload: () => void;
}

interface Entry {
  data: unknown;
  at: number;
}

// Last good result per key: navigation is instant and offline screens keep their data.
const cache = new Map<string, Entry>();
const listeners = new Map<string, Set<() => void>>();

/** Replace a cached value after a local write (start task, verify outlier, …) and re-render users. */
export function setCached<T>(key: string, update: (prev: T | undefined) => T): void {
  cache.set(key, { data: update(cache.get(key)?.data as T | undefined), at: Date.now() });
  listeners.get(key)?.forEach((l) => l());
}

/** Like setCached, but only when `key` is loaded (Realtime patches never create an empty entry). */
export function patchCached<T>(key: string, update: (prev: T) => T): void {
  const entry = cache.get(key);
  if (!entry) return;
  setCached<T>(key, (prev) => update(prev as T));
}

export function getCached<T>(key: string): T | undefined {
  return cache.get(key)?.data as T | undefined;
}

/**
 * Loads `key` once, keeps it in a module cache, and exposes loading / error / stale states.
 * `empty` is what the screen gets under `?state=empty` (mock QA), so every screen's empty state
 * can be checked without editing data.
 */
export function useResource<T>(key: string | null, load: () => Promise<T>, empty: T): Resource<T> {
  const forced = forcedState();
  const cached = key ? (cache.get(key) as Entry | undefined) : undefined;
  const [, force] = useState(0);
  const [state, setState] = useState<{ status: ResourceStatus; error: string | null; stale: boolean }>({
    status: cached ? 'ready' : 'loading',
    error: null,
    stale: false,
  });
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(() => {
    if (!key) return;
    if (forced === 'loading') return;
    if (forced === 'error') {
      setState({ status: 'error', error: 'The server did not answer.', stale: false });
      return;
    }
    let alive = true;
    setState((s) => ({ ...s, status: cache.has(key) ? 'ready' : 'loading' }));
    loadRef
      .current()
      .then((data) => {
        if (!alive) return;
        cache.set(key, { data, at: Date.now() });
        setState({ status: 'ready', error: null, stale: false });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState(cache.has(key) ? { status: 'ready', error: msg, stale: true } : { status: 'error', error: msg, stale: false });
      });
    return () => {
      alive = false;
    };
  }, [key, forced]);

  useEffect(() => run(), [run]);

  useEffect(() => {
    if (!key) return;
    const l = () => force((n) => n + 1);
    let set = listeners.get(key);
    if (!set) listeners.set(key, (set = new Set()));
    set.add(l);
    return () => {
      set?.delete(l);
    };
  }, [key]);

  if (forced === 'loading') return { status: 'loading', data: undefined, error: null, loadedAt: null, stale: false, reload: run };
  if (forced === 'empty') return { status: 'ready', data: empty, error: null, loadedAt: Date.now(), stale: false, reload: run };
  const entry = key ? cache.get(key) : undefined;
  return {
    status: state.status === 'loading' && entry ? 'ready' : state.status,
    data: entry?.data as T | undefined,
    error: state.error,
    loadedAt: entry?.at ?? null,
    stale: state.stale || forced === 'offline',
    reload: run,
  };
}
