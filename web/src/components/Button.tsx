import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode, type Mode } from '@/lib/mode';
import { useHold } from '@/lib/useHold';

export type ButtonVariant = 'primary' | 'secondary' | 'destructive' | 'ghost';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Defaults to the current mode: cab = 64px, office = 40px. */
  size?: Mode;
  icon?: LucideIcon;
  fullWidth?: boolean;
  children: ReactNode;
}

const VARIANT: Record<Mode, Record<ButtonVariant, string>> = {
  cab: {
    primary: 'bg-saffron-500 text-on-saffron hover:bg-saffron-400 active:bg-saffron-600',
    secondary: 'bg-raised text-ink border border-line hover:border-ink-3',
    destructive: 'bg-critical text-on-status hover:opacity-90',
    ghost: 'bg-transparent text-saffron-500 hover:text-saffron-400',
  },
  office: {
    primary: 'bg-saffron-500 text-on-saffron hover:bg-saffron-600',
    secondary: 'bg-surface text-ink border border-line hover:bg-raised',
    destructive: 'bg-surface text-critical border border-critical hover:bg-tint-critical',
    ghost: 'bg-transparent text-ink hover:bg-raised',
  },
};

const SIZE: Record<Mode, string> = {
  cab: 'min-h-touch-cab min-w-touch-cab px-6 gap-3 text-cab-body font-medium',
  office: 'min-h-touch-office min-w-touch-office px-4 gap-2 text-office-body font-medium',
};

export const ICON_SIZE: Record<Mode, number> = { cab: 32, office: 24 };

export function buttonClasses(variant: ButtonVariant, size: Mode, fullWidth = false): string {
  return cx(
    'relative inline-flex select-none items-center justify-center overflow-hidden rounded-md transition-state',
    'disabled:cursor-not-allowed disabled:opacity-50',
    SIZE[size],
    VARIANT[size][variant],
    fullWidth && 'w-full',
  );
}

/** Labels are verbs that say what happens: "Start task", "Report incident". Never "Submit" or "OK". */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size, icon: Icon, fullWidth, className, children, type = 'button', ...rest },
  ref,
) {
  const mode = useMode();
  const s = size ?? mode;
  return (
    <button ref={ref} type={type} className={cx(buttonClasses(variant, s, fullWidth), className)} {...rest}>
      {Icon && <Icon size={ICON_SIZE[s]} strokeWidth={2} aria-hidden />}
      <span>{children}</span>
    </button>
  );
});

export interface HoldButtonProps extends Omit<ButtonProps, 'onClick'> {
  onConfirm: () => void;
  holdMs?: number;
}

/**
 * Press-and-hold button (SOS, "Acknowledge" on a critical takeover). A fill sweeps across while
 * held; releasing early cancels. Keyboard: hold Space or Enter.
 */
export function HoldButton({
  variant = 'primary',
  size,
  icon: Icon,
  fullWidth,
  className,
  children,
  onConfirm,
  holdMs = 1000,
  ...rest
}: HoldButtonProps) {
  const mode = useMode();
  const s = size ?? mode;
  const { progress, handlers } = useHold(onConfirm, holdMs);
  const seconds = holdMs / 1000;
  return (
    <button
      type="button"
      className={cx(buttonClasses(variant, s, fullWidth), 'touch-none', className)}
      aria-description={`Press and hold for ${seconds} second${seconds === 1 ? '' : 's'}`}
      {...handlers}
      {...rest}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-0 bg-current opacity-25"
        style={{ width: `${progress * 100}%` }}
      />
      {Icon && <Icon size={ICON_SIZE[s]} strokeWidth={2} aria-hidden className="relative" />}
      <span className="relative flex flex-col items-start leading-tight">
        <span>{children}</span>
        <span className={cx('font-normal opacity-80', s === 'cab' ? 'text-cab-small' : 'text-office-small')}>
          Hold {seconds} s
        </span>
      </span>
    </button>
  );
}
