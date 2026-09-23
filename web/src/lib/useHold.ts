import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

/**
 * Press-and-hold to confirm (SOS, acknowledge critical). Works with a pointer and with Space/Enter
 * held down. Returns progress 0–1 for the fill and handlers to spread on a button.
 */
export function useHold(onConfirm: () => void, durationMs = 1000) {
  const [progress, setProgress] = useState(0);
  const start = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const confirmRef = useRef(onConfirm);
  confirmRef.current = onConfirm;

  const stop = useCallback(() => {
    if (frame.current != null) cancelAnimationFrame(frame.current);
    frame.current = null;
    start.current = null;
    setProgress(0);
  }, []);

  const tick = useCallback(
    (now: number) => {
      if (start.current == null) return;
      const p = Math.min(1, (now - start.current) / durationMs);
      setProgress(p);
      if (p >= 1) {
        stop();
        confirmRef.current();
        return;
      }
      frame.current = requestAnimationFrame(tick);
    },
    [durationMs, stop],
  );

  const begin = useCallback(() => {
    if (start.current != null) return;
    start.current = performance.now();
    frame.current = requestAnimationFrame(tick);
  }, [tick]);

  useEffect(() => stop, [stop]);

  return {
    progress,
    handlers: {
      onPointerDown: (e: PointerEvent<HTMLElement>) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        begin();
      },
      onPointerUp: stop,
      onPointerCancel: stop,
      onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
        if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
          e.preventDefault();
          begin();
        }
      },
      onKeyUp: (e: KeyboardEvent<HTMLElement>) => {
        if (e.key === ' ' || e.key === 'Enter') stop();
      },
      onBlur: stop,
      onContextMenu: (e: { preventDefault: () => void }) => e.preventDefault(),
    },
  };
}
