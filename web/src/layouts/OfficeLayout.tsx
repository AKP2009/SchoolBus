import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { Bell, HeartPulse, Map, Network, OctagonAlert, ShieldCheck, Shapes, UserRound } from 'lucide-react';
import { OfflineIndicator } from '@/components/OfflineIndicator';
import { cx } from '@/lib/cx';
import { ModeProvider, useDocumentMode } from '@/lib/mode';

export interface OfficeLayoutProps {
  title: string;
  /** Page content. Place children on the 12-column grid with `col-span-*`. */
  children: ReactNode;
  sites?: { id: string; name: string }[];
  siteId?: string;
  onSiteChange?: (id: string) => void;
  /** Open alerts: total and how many are critical or emergency. */
  alertCount?: { open: number; critical: number };
  userName?: string;
  /** Right side of the page title row (filters, buttons). */
  actions?: ReactNode;
  embedded?: boolean;
}

const NAV = [
  { to: '/manager', label: 'Fleet', icon: Map, end: true },
  { to: '/manager/alerts', label: 'Alerts', icon: Bell, end: false },
  { to: '/manager/health', label: 'Maintenance', icon: HeartPulse, end: false },
  { to: '/manager/clusters', label: 'Clusters', icon: Network, end: false },
  { to: '/manager/safety', label: 'Safety', icon: ShieldCheck, end: false },
  { to: '/manager/geofences', label: 'Geofences', icon: Shapes, end: false },
];

function DocumentMode() {
  useDocumentMode('office');
  return null;
}

/**
 * Office mode shell, 1440 reference (design.md §Office mode layout): graphite header with site
 * selector, live alert count and user; side nav; 12-column content grid with 24px gutters.
 */
export function OfficeLayout({
  title,
  children,
  sites = [{ id: 'S1', name: 'S1 · Site' }],
  siteId,
  onSiteChange,
  alertCount = { open: 0, critical: 0 },
  userName = 'Site manager',
  actions,
  embedded,
}: OfficeLayoutProps) {
  return (
    <ModeProvider mode="office" scoped={embedded} className="h-full">
      {!embedded && <DocumentMode />}
      <div className="flex h-full flex-col bg-bg text-ink">
        <header className="flex h-14 shrink-0 items-center gap-6 bg-header px-6 text-header-ink">
          <span className="text-office-h3">Smart Operator</span>
          <label className="flex items-center gap-2 text-office-small text-header-ink-2">
            Site
            <select
              value={siteId ?? sites[0]?.id}
              onChange={(e) => onSiteChange?.(e.target.value)}
              className="min-h-touch-office rounded-sm border border-header-ink-2 bg-header px-2 text-office-body text-header-ink"
            >
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <NavLink
            to="/manager/alerts"
            className="flex min-h-touch-office items-center gap-2 rounded-sm px-2 text-office-body hover:underline"
            aria-label={`${alertCount.open} open alerts, ${alertCount.critical} critical`}
          >
            <Bell size={20} aria-hidden />
            <span className="reading">{alertCount.open}</span>
            <span className="text-header-ink-2">open alerts</span>
            {alertCount.critical > 0 && (
              <span className="ml-1 flex items-center gap-1 rounded-sm border border-critical px-2 text-office-small">
                <OctagonAlert size={16} className="text-critical" aria-hidden />
                <span className="reading">{alertCount.critical}</span> critical
              </span>
            )}
          </NavLink>
          <div className="ml-auto flex items-center gap-6">
            <OfflineIndicator onHeader />
            <span className="flex items-center gap-2 text-office-body">
              <UserRound size={20} aria-hidden />
              {userName}
            </span>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <nav className="w-56 shrink-0 border-r bg-surface p-3" aria-label="Manager">
            <ul className="flex flex-col gap-1">
              {NAV.map(({ to, label, icon: Icon, end }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={end}
                    className={({ isActive }) =>
                      cx(
                        'flex min-h-touch-office items-center gap-3 rounded-md px-3 text-office-body transition-state',
                        isActive ? 'bg-saffron-500 text-on-saffron' : 'text-ink hover:bg-raised',
                      )
                    }
                  >
                    <Icon size={20} aria-hidden />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <main className="min-w-0 flex-1 overflow-auto p-6">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
              <h1 className="text-office-h1">{title}</h1>
              {actions}
            </div>
            <div className="grid grid-cols-12 gap-6">{children}</div>
          </main>
        </div>
      </div>
    </ModeProvider>
  );
}
