import { openDB, type DBSchema } from 'idb';
import { useConnection } from '@/stores/connection';

/** A write made while offline. `client_id` makes the later upsert idempotent (CLAUDE.md rule 7). */
export interface QueuedWrite {
  client_id: string;
  table: 'incidents' | 'tasks' | 'training_records';
  payload: Record<string, unknown>;
  queued_at: string;
}

interface QueueDB extends DBSchema {
  writes: { key: string; value: QueuedWrite };
}

const db = () =>
  openDB<QueueDB>('operator-offline', 1, {
    upgrade(d) {
      d.createObjectStore('writes', { keyPath: 'client_id' });
    },
  });

async function refreshCount() {
  useConnection.getState().setQueued(await (await db()).count('writes'));
}

export async function enqueue(write: Omit<QueuedWrite, 'queued_at'>): Promise<void> {
  await (await db()).put('writes', { ...write, queued_at: new Date().toISOString() });
  await refreshCount();
}

/** Replays queued writes oldest first; `send` should upsert on client_id and throw on failure. */
export async function flush(send: (w: QueuedWrite) => Promise<void>): Promise<void> {
  const d = await db();
  const all = (await d.getAll('writes')).sort((a, b) => a.queued_at.localeCompare(b.queued_at));
  for (const w of all) {
    await send(w);
    await d.delete('writes', w.client_id);
  }
  await refreshCount();
}
