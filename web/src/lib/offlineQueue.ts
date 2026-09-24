import { openDB, type DBSchema } from 'idb';
import { useConnection } from '@/stores/connection';

/**
 * A write made while offline. `client_id` makes the later upsert idempotent (CLAUDE.md rule 7):
 * incidents and training_records carry it as a column; a task update is a plain update by task_id,
 * which is idempotent by itself, and uses client_id only as the queue key.
 */
export interface QueuedWrite {
  client_id: string;
  table: 'incidents' | 'tasks' | 'training_records';
  payload: Record<string, unknown>;
  queued_at: string;
  /** Failed sends so far and the last error (shown nowhere yet; kept for debugging). */
  attempts?: number;
  last_error?: string;
}

interface QueueDB extends DBSchema {
  writes: { key: string; value: QueuedWrite };
}

let dbp: ReturnType<typeof open> | null = null;
function open() {
  return openDB<QueueDB>('operator-offline', 1, {
    upgrade(d) {
      d.createObjectStore('writes', { keyPath: 'client_id' });
    },
  });
}
const db = () => (dbp ??= open());

export async function refreshCount(): Promise<void> {
  try {
    useConnection.getState().setQueued(await (await db()).count('writes'));
  } catch {
    /* IndexedDB unavailable (private mode): nothing can be queued either */
  }
}

export async function queuedIds(): Promise<string[]> {
  return (await (await db()).getAllKeys('writes')).map(String);
}

export async function enqueue(write: Omit<QueuedWrite, 'queued_at'>): Promise<void> {
  await (await db()).put('writes', { ...write, queued_at: new Date().toISOString() });
  await refreshCount();
}

let flushing: Promise<number> | null = null;

/**
 * Replays queued writes oldest first; `send` should upsert on client_id and throw on failure.
 * A failed write stays queued and the rest still go (one bad row can't block the cab). Only one
 * flush runs at a time. Resolves to the number of writes still queued.
 */
export function flush(send: (w: QueuedWrite) => Promise<void>): Promise<number> {
  flushing ??= (async () => {
    try {
      const d = await db();
      const all = (await d.getAll('writes')).sort((a, b) => a.queued_at.localeCompare(b.queued_at));
      let left = 0;
      for (const w of all) {
        try {
          await send(w);
          await d.delete('writes', w.client_id);
        } catch (e) {
          left++;
          await d.put('writes', { ...w, attempts: (w.attempts ?? 0) + 1, last_error: e instanceof Error ? e.message : String(e) });
        }
        await refreshCount();
      }
      return left;
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}
