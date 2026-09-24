import { useEffect, useRef } from 'react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { STATUS } from '@/lib/status';
import type { Alert } from '@/types/domain';
import { HoldButton } from './Button';
import { useT } from '@/i18n';

export interface AlertTakeoverProps {
  /** Every open critical/emergency alert. Only the first (emergency first, then oldest) is shown. */
  queue: Alert[];
  /** Called after a 1-second hold on "Acknowledge". Emergencies are resolved by the manager, not here. */
  onAcknowledge: (id: number) => void;
  className?: string;
}

const RANK = { emergency: 0, critical: 1, warning: 2, info: 3 } as const;

export function orderTakeovers(queue: Alert[]): Alert[] {
  return queue
    .filter((a) => a.severity === 'critical' || a.severity === 'emergency')
    .sort((a, b) => RANK[a.severity] - RANK[b.severity] || a.ts.localeCompare(b.ts));
}

/**
 * One alert owns the screen (design.md principle 4). Covers its positioned parent — in CabLayout
 * that is the primary zone, so the safety zone, Voice and SOS stay reachable.
 */
export function AlertTakeover({ queue, onAcknowledge, className }: AlertTakeoverProps) {
  const mode = useMode();
  const t = useT();
  const ordered = orderTakeovers(queue);
  const alert = ordered[0];
  const panel = useRef<HTMLDivElement>(null);

  // Move focus to the takeover so keyboard/switch users land on the acknowledge button.
  useEffect(() => {
    panel.current?.focus();
  }, [alert?.id]);

  if (!alert) return null;
  const emergency = alert.severity === 'emergency';
  const meta = STATUS[alert.severity];
  const Icon = meta.icon;
  const waiting = ordered.length - 1;
  const cab = mode === 'cab';
  const instruction = alert.recommended_action ?? alert.message;

  return (
    <div className={cx('absolute inset-0 z-20 p-4', className)}>
      <div
        ref={panel}
        tabIndex={-1}
        role="alert"
        aria-live="assertive"
        className={cx(
          'flex h-full flex-col rounded-lg border-4 bg-raised outline-none',
          meta.border,
          emergency ? 'anim-emergency' : 'anim-takeover',
          cab ? 'gap-6 p-8' : 'gap-4 p-6 shadow-float',
        )}
      >
        <div className="flex items-start justify-between gap-4">
          <div className={cx('flex items-center gap-3 rounded-md px-3 py-2', meta.tint)}>
            <Icon size={cab ? 48 : 32} strokeWidth={2} className={meta.text} aria-hidden />
            <span className={cx('font-bold', cab ? 'text-cab-h2' : 'text-office-h2')}>{t(`status.${alert.severity}`)}</span>
          </div>
          {waiting > 0 && (
            <span
              className={cx(
                'rounded-sm border bg-surface px-3 py-1 text-ink-2',
                cab ? 'text-cab-small' : 'text-office-small',
              )}
            >
              {t.rich('alert.moreWaiting', { n: <span className="reading text-ink">+{waiting}</span> })}
            </span>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <h2 className={cab ? 'text-cab-h1' : 'text-office-h1'}>{alert.title}</h2>
          <p className={cx('mt-2', cab ? 'text-cab-h2' : 'text-office-h2')}>{instruction}</p>
          {alert.steps && alert.steps.length > 0 && (
            <ol className={cx('mt-4 list-decimal space-y-2 pl-8', cab ? 'text-cab-body' : 'text-office-body')}>
              {alert.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </div>

        <div className="flex items-center justify-between gap-4">
          {emergency ? (
            <p className={cx('text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>
              {t('alert.managerNotified')}
            </p>
          ) : (
            <>
              {alert.machine_id && (
                <span className={cx('reading text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>
                  {alert.machine_id}
                </span>
              )}
              <HoldButton variant="destructive" onConfirm={() => onAcknowledge(alert.id)} className="ml-auto">
                {t('alert.acknowledge')}
              </HoldButton>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
