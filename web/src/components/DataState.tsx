import type { ReactNode } from 'react';
import { CloudOff, RotateCw, type LucideIcon } from 'lucide-react';
import { cx } from '@/lib/cx';
import { fmtTime } from '@/lib/format';
import { useMode } from '@/lib/mode';
import { useConnection } from '@/stores/connection';
import type { Resource } from '@/data/resource';
import { Button } from './Button';
import { EmptyState, Skeleton } from './EmptyState';
import { useT } from '@/i18n';

export interface DataStateProps<T> {
  res: Resource<T>;
  /** Screen-specific empty test; default: null/undefined or an empty array. */
  isEmpty?: (data: T) => boolean;
  empty: { icon?: LucideIcon; title: string; hint: string; action?: ReactNode };
  /** "Couldn't load tasks." */
  errorTitle: string;
  /** Skeleton layout while loading; default three blocks. */
  skeleton?: ReactNode;
  children: (data: NonNullable<T>) => ReactNode;
  className?: string;
}

/**
 * Loading → skeleton, error → what happened + what to do, empty → what will appear and how,
 * offline with saved data → the data plus a "saved at" note (design.md §Empty, loading, offline, error).
 */
export function DataState<T>({ res, isEmpty, empty, errorTitle, skeleton, children, className }: DataStateProps<T>) {
  const cab = useMode() === 'cab';
  const online = useConnection((s) => s.online);
  const t = useT();

  if (res.status === 'loading') {
    return (
      <div className={cx('flex flex-col', cab ? 'gap-4' : 'gap-3', className)} aria-busy="true" aria-label={t('data.loading')}>
        {skeleton ?? (
          <>
            <Skeleton className={cab ? 'h-10 w-1/2' : 'h-7 w-1/3'} />
            <Skeleton className={cab ? 'h-32' : 'h-24'} />
            <Skeleton className={cab ? 'h-32' : 'h-24'} />
          </>
        )}
      </div>
    );
  }

  if (res.status === 'error' || res.data === undefined) {
    return (
      <EmptyState
        icon={CloudOff}
        title={errorTitle}
        hint={online ? t('data.tryLater', { error: res.error ?? t('data.noAnswer') }) : t('data.offlineHint')}
        action={
          <Button variant="secondary" icon={RotateCw} onClick={res.reload}>
            {t('data.tryAgain')}
          </Button>
        }
        className={className}
      />
    );
  }

  const data = res.data;
  const blank = isEmpty ? isEmpty(data) : data == null || (Array.isArray(data) && data.length === 0);
  if (blank) return <EmptyState icon={empty.icon} title={empty.title} hint={empty.hint} action={empty.action} className={className} />;

  return (
    <>
      {(res.stale || !online) && res.loadedAt != null && (
        <p className={cx('flex items-center gap-2 text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>
          <CloudOff size={cab ? 22 : 16} aria-hidden />
          {t.rich('data.savedAt', { time: <span className="reading">{fmtTime(res.loadedAt)}</span> })}
        </p>
      )}
      {children(data as NonNullable<T>)}
    </>
  );
}
