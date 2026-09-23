import type { ReactNode } from 'react';
import { Inbox, type LucideIcon } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';

export interface EmptyStateProps {
  icon?: LucideIcon;
  /** What will appear: "No tasks yet." */
  title: string;
  /** How to get it: "Your supervisor will assign today's work." */
  hint: string;
  action?: ReactNode;
  className?: string;
}

/** Empty, error or loading-failed states. Say what happened and what to do; never apologise. */
export function EmptyState({ icon: Icon = Inbox, title, hint, action, className }: EmptyStateProps) {
  const mode = useMode();
  const cab = mode === 'cab';
  return (
    <div
      className={cx(
        'flex flex-col items-center justify-center rounded-md border border-dashed text-center',
        cab ? 'gap-3 p-8' : 'gap-2 p-6',
        className,
      )}
    >
      <Icon size={cab ? 48 : 32} strokeWidth={2} className="text-ink-2" aria-hidden />
      <p className={cab ? 'text-cab-h2' : 'text-office-h3'}>{title}</p>
      <p className={cx('max-w-[60ch] text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>{hint}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Skeleton block for loading states (no spinners). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx('rounded-md bg-raised', className)} />;
}
