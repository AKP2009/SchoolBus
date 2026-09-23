import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { FileWarning, Fuel, GraduationCap, ListChecks, Mic, Siren, Timer, Truck } from 'lucide-react';
import { AlertTakeover } from '@/components/AlertTakeover';
import { Button, HoldButton } from '@/components/Button';
import { OfflineIndicator } from '@/components/OfflineIndicator';
import { cx } from '@/lib/cx';
import { ModeProvider, useDocumentMode } from '@/lib/mode';
import { useAlerts } from '@/stores/alerts';

export interface CabStatus {
  machineId: string;
  /** Minutes since shift start. */
  shiftMinutes: number;
  fuelPct: number;
}

export interface CabLayoutProps {
  status: CabStatus;
  /** Current task, time left, next task. A warning AlertBanner goes at the top of this zone. */
  primary: ReactNode;
  /** SafetyPanel. */
  safety: ReactNode;
  onVoice?: () => void;
  /** Fired after the 1-second hold. Defaults to raising a local emergency takeover. */
  onSos?: () => void;
  /** Styleguide: don't touch <html>, scope the cab palette to this element instead. */
  embedded?: boolean;
  className?: string;
}

const NAV = [
  { to: '/operator', label: 'Tasks', icon: ListChecks, end: true },
  { to: '/operator/machine', label: 'Machine', icon: Truck, end: false },
  { to: '/operator/training', label: 'Training', icon: GraduationCap, end: false },
  { to: '/operator/report', label: 'Report', icon: FileWarning, end: false },
];

function formatShift(min: number) {
  return `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, '0')}m`;
}

function DocumentMode() {
  useDocumentMode('cab');
  return null;
}

/**
 * Cab mode shell, 1280 × 800 reference (design.md §Cab mode layout): 56px status bar, primary zone,
 * safety zone, 88px bottom bar with nav on the left and Voice + SOS always visible on the right.
 */
export function CabLayout({ status, primary, safety, onVoice, onSos, embedded, className }: CabLayoutProps) {
  const { takeovers, acknowledge, push } = useAlerts();

  const sos =
    onSos ??
    (() =>
      push({
        id: Date.now(),
        ts: new Date().toISOString(),
        machine_id: status.machineId,
        title: 'SOS sent',
        message: 'Your location and machine state were sent to the site manager.',
        recommended_action: 'Stay in the cab if it is safe. Help is on the way.',
        severity: 'emergency',
        stage: 'escalated',
      }));

  return (
    <ModeProvider mode="cab" scoped={embedded} className="h-full">
      {!embedded && <DocumentMode />}
      <div className={cx('flex h-full flex-col bg-bg text-ink', className)}>
        <header className="flex h-status-bar shrink-0 items-center gap-6 border-b bg-surface px-6 text-cab-small">
          <span className="reading font-medium text-cab-body">{status.machineId}</span>
          <span className="flex items-center gap-2">
            <Timer size={24} className="text-ink-2" aria-hidden />
            <span className="text-ink-2">Shift</span>
            <span className="reading">{formatShift(status.shiftMinutes)}</span>
          </span>
          <span className="flex items-center gap-2">
            <Fuel size={24} className="text-ink-2" aria-hidden />
            <span className="text-ink-2">Fuel</span>
            <span className="reading">{status.fuelPct}%</span>
          </span>
          <OfflineIndicator className="ml-auto" />
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[1fr_minmax(0,520px)]">
          <main className="relative min-h-0">
            <div className="h-full overflow-auto p-6">{primary}</div>
            <AlertTakeover queue={takeovers} onAcknowledge={acknowledge} />
          </main>
          <aside className="min-h-0 overflow-auto border-l bg-surface p-6" aria-label="Safety zone">
            {safety}
          </aside>
        </div>

        <footer className="flex h-bottom-bar shrink-0 items-center gap-4 border-t bg-surface px-4">
          <nav className="flex gap-2" aria-label="Operator">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cx(
                    'flex min-h-touch-cab min-w-touch-cab items-center gap-2 rounded-md px-4 text-cab-body transition-state',
                    isActive ? 'bg-saffron-500 text-on-saffron' : 'text-ink hover:bg-raised',
                  )
                }
              >
                <Icon size={28} aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-8">
            <Button variant="secondary" icon={Mic} onClick={onVoice}>
              Voice
            </Button>
            {/* SOS stays far from normal buttons (gap-8 + divider) and needs a 1-second hold. */}
            <div className="h-12 border-l" aria-hidden />
            <HoldButton variant="destructive" icon={Siren} onConfirm={sos} aria-label="SOS, press and hold">
              SOS
            </HoldButton>
          </div>
        </footer>
      </div>
    </ModeProvider>
  );
}
