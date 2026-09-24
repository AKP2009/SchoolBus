import { useEffect } from 'react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { STATUS } from '@/lib/status';
import { Button } from './Button';
import { useT } from '@/i18n';

export interface AlertBannerProps {
  level: 'info' | 'warning';
  /** What: "Hydraulic oil hot — 94 °C" */
  title: string;
  /** What to do: "Switch to economy mode and reduce load" */
  instruction: string;
  /** Warning banners need an action; defaults to "Got it". */
  actionLabel?: string;
  onDismiss: () => void;
  /** Info auto-dismisses after 5 s (design.md §Alerts). */
  autoDismissMs?: number;
  className?: string;
}

/** Warning banner at the top of the primary zone, or an info toast. Critical alerts use AlertTakeover. */
export function AlertBanner({
  level,
  title,
  instruction,
  actionLabel,
  onDismiss,
  autoDismissMs = level === 'info' ? 5000 : undefined,
  className,
}: AlertBannerProps) {
  const mode = useMode();
  const t = useT();
  const meta = STATUS[level];
  const Icon = meta.icon;

  useEffect(() => {
    if (autoDismissMs == null) return;
    const t = window.setTimeout(onDismiss, autoDismissMs);
    return () => window.clearTimeout(t);
  }, [autoDismissMs, onDismiss]);

  const cab = mode === 'cab';
  return (
    <div
      role="status"
      aria-live="polite"
      className={cx(
        'flex items-center rounded-md border-l-8 bg-surface',
        meta.border,
        cab ? 'gap-4 p-4' : 'gap-3 border border-l-8 p-3',
        className,
      )}
    >
      <div className={cx('flex shrink-0 items-center gap-2 self-start rounded-sm px-2 py-1', meta.tint)}>
        <Icon size={cab ? 32 : 24} strokeWidth={2} className={meta.text} aria-hidden />
        <span className={cx('font-medium', cab ? 'text-cab-small' : 'text-office-small')}>{t(`status.${level}`)}</span>
      </div>
      <div className="min-w-0 flex-1">
        <p className={cab ? 'text-cab-h2' : 'text-office-h3'}>{title}</p>
        <p className={cx('text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>{instruction}</p>
      </div>
      {level === 'warning' && (
        <Button variant="secondary" onClick={onDismiss} className="shrink-0">
          {actionLabel ?? t('alert.gotIt')}
        </Button>
      )}
    </div>
  );
}
