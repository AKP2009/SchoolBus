import { cx } from '@/lib/cx';
import { useMode, type Mode } from '@/lib/mode';
import { STATUS, type Status } from '@/lib/status';

export interface StatusBadgeProps {
  status: Status;
  /** Overrides the default word ("Clear" instead of "OK"). Never pass an empty string. */
  label?: string;
  size?: Mode;
  className?: string;
}

/**
 * Colour + icon + word, always all three (design.md principle 3). The word stays in ink colour
 * so it keeps AA contrast on the tint; the icon and outline carry the status colour.
 */
export function StatusBadge({ status, label, size, className }: StatusBadgeProps) {
  const mode = useMode();
  const s = size ?? mode;
  const meta = STATUS[status];
  const Icon = meta.icon;
  return (
    <span
      className={cx(
        'inline-flex items-center whitespace-nowrap rounded-sm border font-medium text-ink',
        meta.tint,
        meta.border,
        s === 'cab' ? 'gap-2 px-3 py-1 text-cab-small' : 'gap-1.5 px-2 py-0.5 text-office-small',
        className,
      )}
    >
      <Icon size={s === 'cab' ? 24 : 16} strokeWidth={2} className={meta.text} aria-hidden />
      <span>{label ?? meta.word}</span>
    </span>
  );
}
