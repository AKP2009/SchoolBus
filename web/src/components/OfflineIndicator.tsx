import { Clock, Wifi, WifiOff } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { useConnection } from '@/stores/connection';

export interface OfflineIndicatorProps {
  /** Override the live navigator.onLine value (styleguide, demo "offline" scenario). */
  online?: boolean;
  /** Writes waiting in the IndexedDB queue. */
  queued?: number;
  /** On a graphite header the text uses the header ink tokens. */
  onHeader?: boolean;
  className?: string;
}

/** Status-bar connection state. Offline: wifi-off + "Offline — changes will sync" (design.md §Offline). */
export function OfflineIndicator({ online, queued, onHeader, className }: OfflineIndicatorProps) {
  const mode = useMode();
  const live = useConnection();
  const isOnline = online ?? live.online;
  const pending = queued ?? live.queued;
  const cab = mode === 'cab';
  const size = cab ? 24 : 16;
  const text = cab ? 'text-cab-small' : 'text-office-small';

  return (
    <span
      role="status"
      aria-live="polite"
      className={cx('inline-flex items-center gap-2', text, onHeader ? 'text-header-ink' : 'text-ink', className)}
    >
      {isOnline ? (
        <>
          <Wifi size={size} className="text-ok" aria-hidden />
          <span>Online</span>
        </>
      ) : (
        <>
          <WifiOff size={size} className="text-offline" aria-hidden />
          <span>Offline — changes will sync</span>
        </>
      )}
      {pending > 0 && (
        <span className={cx('inline-flex items-center gap-1', onHeader ? 'text-header-ink-2' : 'text-ink-2')}>
          <Clock size={size - 4} aria-hidden />
          <span className="reading">{pending}</span> queued
        </span>
      )}
    </span>
  );
}
