import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { FileWarning, Fuel, GraduationCap, ListChecks, Mic, ShieldCheck, Siren, Timer, Truck } from 'lucide-react';
import { AlertBanner } from '@/components/AlertBanner';
import { AlertTakeover } from '@/components/AlertTakeover';
import { Button, HoldButton } from '@/components/Button';
import { OfflineIndicator } from '@/components/OfflineIndicator';
import { cx } from '@/lib/cx';
import { ModeProvider, useDocumentMode } from '@/lib/mode';
import type { Alert } from '@/types/domain';

export interface CabStatus {
  machineId: string;
  /** Minutes since shift start. */
  shiftMinutes: number;
  /** null = no reading yet. */
  fuelPct: number | null;
}

export interface CabLayoutProps {
  status: CabStatus;
  /** Current task, time left, next task. */
  primary: ReactNode;
  /** SafetyPanel. */
  safety: ReactNode;
  /** Open critical/emergency alerts; the takeover shows one and counts the rest. */
  takeovers?: Alert[];
  onAcknowledge?: (alert: Alert) => void;
  /** Warning (or info) banner at the top of the primary zone. */
  banner?: Alert | null;
  onDismissBanner?: (alert: Alert) => void;
  /** Short note in the status bar area (info toast), e.g. "Voice isn't connected yet". */
  toast?: string | null;
  onVoice?: () => void;
  /** Fired after the 1-second hold. */
  onSos?: () => void;
  /** Styleguide: don't touch <html>, scope the cab palette to this element instead. */
  embedded?: boolean;
  /** Hide nav (sign-in screen). */
  bare?: boolean;
  className?: string;
}

const NAV = [
  { to: '/operator', label: 'Tasks', icon: ListChecks, end: true },
  { to: '/operator/machine', label: 'Machine', icon: Truck, end: false },
  { to: '/operator/safety', label: 'Safety', icon: ShieldCheck, end: false },
  { to: '/operator/training', label: 'Training', icon: GraduationCap, end: false },
  { to: '/operator/report', label: 'Report', icon: FileWarning, end: false },
];

function formatShift(min: number) {
  const m = Math.max(0, Math.floor(min));
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function DocumentMode() {
  useDocumentMode('cab');
  return null;
}

/**
 * Cab mode shell, 1280 × 800 reference (design.md §Cab mode layout): 56px status bar, primary zone,
 * safety zone, 88px bottom bar with nav on the left and Voice + SOS always visible on the right.
 */
export function CabLayout({
  status,
  primary,
  safety,
  takeovers = [],
  onAcknowledge,
  banner,
  onDismissBanner,
  toast,
  onVoice,
  onSos,
  embedded,
  bare,
  className,
}: CabLayoutProps) {
  return (
    <ModeProvider mode="cab" scoped={embedded} className="h-full">
      {!embedded && <DocumentMode />}
      <div className={cx('flex h-full flex-col bg-bg text-ink', className)}>
        <header className="relative flex h-status-bar shrink-0 items-center gap-6 border-b bg-surface px-6 text-cab-small">
          <span className="reading font-medium text-cab-body">{status.machineId}</span>
          {!bare && (<><span className="flex items-center gap-2">
            <Timer size={24} className="text-ink-2" aria-hidden />
            <span className="text-ink-2">Shift</span>
            <span className="reading">{formatShift(status.shiftMinutes)}</span>
          </span>
          <span className="flex items-center gap-2">
            <Fuel size={24} className="text-ink-2" aria-hidden />
            <span className="text-ink-2">Fuel</span>
            <span className="reading">{status.fuelPct == null ? '—' : `${Math.round(status.fuelPct)}%`}</span>
          </span></>)}
          {toast && (
            <span role="status" aria-live="polite" className="truncate rounded-sm border border-info bg-tint-info px-3 py-1 text-ink">
              {toast}
            </span>
          )}
          <OfflineIndicator className="ml-auto shrink-0" />
        </header>

        <div className={cx('grid min-h-0 flex-1', bare ? 'grid-cols-1' : 'grid-cols-[minmax(0,1fr)_480px]')}>
          <main className="relative min-h-0">
            <div className="flex h-full flex-col gap-4 overflow-auto p-6">
              {banner && (banner.severity === 'warning' || banner.severity === 'info') && (
                <AlertBanner
                  level={banner.severity}
                  title={banner.title}
                  instruction={banner.recommended_action ?? banner.message}
                  onDismiss={() => onDismissBanner?.(banner)}
                  className="shrink-0"
                />
              )}
              {primary}
            </div>
            <AlertTakeover queue={takeovers} onAcknowledge={(id) => {
              const a = takeovers.find((x) => x.id === id);
              if (a) onAcknowledge?.(a);
            }} />
          </main>
          {!bare && (
            <aside className="min-h-0 overflow-auto border-l bg-surface p-6" aria-label="Safety zone">
              {safety}
            </aside>
          )}
        </div>

        {!bare && (
          <footer className="flex h-bottom-bar shrink-0 items-center gap-4 border-t bg-surface px-4">
            <nav className="flex gap-1" aria-label="Operator">
              {NAV.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cx(
                      'flex min-h-touch-cab min-w-touch-cab items-center gap-2 rounded-md px-3 text-cab-body transition-state',
                      isActive ? 'bg-saffron-500 text-on-saffron' : 'text-ink hover:bg-raised',
                    )
                  }
                >
                  <Icon size={28} aria-hidden />
                  {label}
                </NavLink>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-6">
              <Button variant="secondary" icon={Mic} onClick={onVoice}>
                Voice
              </Button>
              {/* SOS stays far from normal buttons (gap + divider) and needs a 1-second hold. */}
              <div className="h-12 border-l" aria-hidden />
              <HoldButton variant="destructive" icon={Siren} onConfirm={() => onSos?.()} aria-label="SOS, press and hold">
                SOS
              </HoldButton>
            </div>
          </footer>
        )}
      </div>
    </ModeProvider>
  );
}
