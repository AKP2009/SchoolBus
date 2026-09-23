import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ClipboardList, CloudOff, Plus, Trash2 } from 'lucide-react';
import { AlertBanner } from '@/components/AlertBanner';
import { AlertTakeover } from '@/components/AlertTakeover';
import { Button, HoldButton } from '@/components/Button';
import { DigitalTwin } from '@/components/DigitalTwin';
import { EmptyState, Skeleton } from '@/components/EmptyState';
import { Gauge } from '@/components/Gauge';
import { KpiTile } from '@/components/KpiTile';
import { OfflineIndicator } from '@/components/OfflineIndicator';
import { SafetyPanel } from '@/components/SafetyPanel';
import { StatusBadge } from '@/components/StatusBadge';
import { TaskCard } from '@/components/TaskCard';
import { CabLayout } from '@/layouts/CabLayout';
import { OfficeLayout } from '@/layouts/OfficeLayout';
import { ModeProvider, useDocumentMode, type Mode } from '@/lib/mode';
import type { Status } from '@/lib/status';
import {
  sampleEmergency,
  sampleSafety,
  sampleSafetyClear,
  sampleStatusBar,
  sampleTakeovers,
  sampleTasks,
  sampleTwin,
  sampleWarning,
} from '@/mocks/sample';
import type { Alert } from '@/types/domain';

const MODES: Mode[] = ['cab', 'office'];
const STATUSES: Status[] = ['ok', 'info', 'warning', 'critical', 'emergency', 'offline', 'unknown'];

/** Renders `render(mode)` twice: once on the dark cab palette, once on the light office palette. */
function BothModes({ render }: { render: (mode: Mode) => ReactNode }) {
  return (
    <div className="flex flex-col gap-4">
      {MODES.map((m) => (
        <ModeProvider key={m} mode={m} scoped className="rounded-lg border bg-bg p-6 text-ink">
          <p className={m === 'cab' ? 'mb-4 text-cab-small text-ink-2' : 'mb-3 text-office-small text-ink-2'}>
            {m === 'cab' ? 'Cab mode' : 'Office mode'}
          </p>
          {render(m)}
        </ModeProvider>
      ))}
    </div>
  );
}

function Section({ id, title, note, children }: { id: string; title: string; note: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6">
      <h2 className="text-office-h2">{title}</h2>
      <p className="mb-4 mt-1 max-w-[75ch] text-office-body text-ink-2">{note}</p>
      {children}
    </section>
  );
}

function TakeoverDemo() {
  const [queue, setQueue] = useState<Alert[]>(sampleTakeovers);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" icon={Plus} onClick={() => setQueue(sampleTakeovers)}>
          Reset critical alerts
        </Button>
        <Button variant="secondary" icon={Plus} onClick={() => setQueue((q) => [...q, { ...sampleEmergency, id: Date.now() }])}>
          Add emergency
        </Button>
      </div>
      <div className="relative h-[460px] rounded-md border bg-surface p-6">
        <p className="text-ink-2">Primary zone content sits under the takeover.</p>
        <AlertTakeover queue={queue} onAcknowledge={(id) => setQueue((q) => q.filter((a) => a.id !== id))} />
        {queue.length === 0 && <p className="mt-2">All alerts acknowledged.</p>}
      </div>
    </div>
  );
}

function BannerDemo() {
  const [shown, setShown] = useState({ warning: true, info: true });
  return (
    <div className="flex flex-col gap-3">
      {shown.warning && (
        <AlertBanner level="warning" {...sampleWarning} onDismiss={() => setShown((s) => ({ ...s, warning: false }))} />
      )}
      {shown.info && (
        <AlertBanner
          level="info"
          title="Rain expected at 14:00"
          instruction="Task times after 14:00 now include rain"
          onDismiss={() => setShown((s) => ({ ...s, info: false }))}
          autoDismissMs={60000}
        />
      )}
      {(!shown.warning || !shown.info) && (
        <Button variant="ghost" onClick={() => setShown({ warning: true, info: true })} className="self-start">
          Show banners again
        </Button>
      )}
    </div>
  );
}

const TOC = [
  ['tokens', 'Tokens'],
  ['buttons', 'Button'],
  ['status', 'StatusBadge'],
  ['banner', 'AlertBanner'],
  ['takeover', 'AlertTakeover'],
  ['gauge', 'Gauge'],
  ['task', 'TaskCard'],
  ['safety', 'SafetyPanel'],
  ['twin', 'DigitalTwin'],
  ['kpi', 'KpiTile'],
  ['empty', 'EmptyState'],
  ['offline', 'OfflineIndicator'],
  ['layouts', 'Layouts'],
] as const;

const SWATCHES = [
  ['bg-saffron-500', 'saffron-500'],
  ['bg-bg', 'bg'],
  ['bg-surface', 'surface'],
  ['bg-raised', 'raised'],
  ['bg-line', 'border'],
  ['bg-ink', 'text'],
  ['bg-ink-2', 'text-2'],
  ['bg-ok', 'ok'],
  ['bg-info', 'info'],
  ['bg-warning', 'warning'],
  ['bg-critical', 'critical'],
  ['bg-offline', 'offline'],
] as const;

export function Styleguide() {
  useDocumentMode('office');
  return (
    <ModeProvider mode="office">
      <div className="min-h-full bg-bg text-ink">
        <header className="flex items-center gap-6 bg-header px-6 py-4 text-header-ink">
          <h1 className="text-office-h1">Styleguide</h1>
          <p className="text-office-body text-header-ink-2">Every shared component in cab and office mode, on sample data.</p>
          <nav className="ml-auto flex gap-4 text-office-body">
            <Link className="underline" to="/operator">
              Cab layout (1280 × 800)
            </Link>
            <Link className="underline" to="/manager">
              Office layout (1440)
            </Link>
          </nav>
        </header>

        <div className="flex">
          <nav className="sticky top-0 hidden h-screen w-48 shrink-0 overflow-auto border-r p-4 lg:block" aria-label="Components">
            <ul className="flex flex-col gap-1 text-office-body">
              {TOC.map(([id, label]) => (
                <li key={id}>
                  <a className="flex min-h-touch-office items-center rounded-md px-2 hover:bg-raised" href={`#${id}`}>
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <main className="flex min-w-0 flex-1 flex-col gap-12 p-6">
            <Section id="tokens" title="Tokens" note="All colours come from web/src/styles/tokens.css. The same class resolves to the cab or office value by data-mode.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-6 gap-3">
                    {SWATCHES.map(([cls, name]) => (
                      <div key={name} className="flex flex-col gap-1">
                        <div className={`h-12 rounded-md border ${cls}`} />
                        <span className="reading text-office-small text-ink-2">{name}</span>
                      </div>
                    ))}
                  </div>
                )}
              />
            </Section>

            <Section id="buttons" title="Button" note="Cab buttons are 64px high for gloves, office 40px. Labels are verbs. SOS and critical acknowledge use a 1-second hold.">
              <BothModes
                render={() => (
                  <div className="flex flex-wrap items-center gap-4">
                    <Button>Start task</Button>
                    <Button variant="secondary">Report incident</Button>
                    <Button variant="destructive" icon={Trash2}>
                      Cancel task
                    </Button>
                    <Button variant="ghost">View history</Button>
                    <Button disabled>Complete task</Button>
                    <HoldButton variant="destructive" onConfirm={() => undefined}>
                      Acknowledge
                    </HoldButton>
                  </div>
                )}
              />
            </Section>

            <Section id="status" title="StatusBadge" note="Colour, icon and word, always all three.">
              <BothModes
                render={() => (
                  <div className="flex flex-wrap gap-3">
                    {STATUSES.map((s) => (
                      <StatusBadge key={s} status={s} />
                    ))}
                    <StatusBadge status="ok" label="Clear" />
                  </div>
                )}
              />
            </Section>

            <Section id="banner" title="AlertBanner" note="Warning: banner at the top of the primary zone with “Got it”. Info: toast, auto-dismisses after 5 s (60 s here so you can see it).">
              <BothModes render={() => <BannerDemo />} />
            </Section>

            <Section id="takeover" title="AlertTakeover" note="One alert owns the screen; emergency first, then oldest. Others wait behind a count. Hold “Acknowledge” for 1 s. Emergencies are resolved by the manager.">
              <BothModes render={() => <TakeoverDemo />} />
            </Section>

            <Section id="gauge" title="Gauge" note="Horizontal bar, faint normal-range band, DM Mono reading, trend arrow when moving.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-3 gap-8">
                    <Gauge label="Coolant" value={88} unit="°C" min={40} max={120} normal={[75, 100]} />
                    <Gauge label="Hydraulic oil" value={94} unit="°C" min={20} max={110} normal={[40, 85]} trend="rising" />
                    <Gauge
                      label="Oil pressure"
                      value={180}
                      unit="kPa"
                      min={0}
                      max={600}
                      normal={[250, 500]}
                      status="critical"
                      trend="falling"
                    />
                  </div>
                )}
              />
            </Section>

            <Section id="task" title="TaskCard" note="Type + material, quantity, predicted time with its range, the two biggest factors, and the state button.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-2 gap-4">
                    {sampleTasks.map((t, i) => (
                      <TaskCard key={t.task_id} task={t} position={i + 1} pendingSync={i === 2} />
                    ))}
                  </div>
                )}
              />
            </Section>

            <Section id="safety" title="SafetyPanel" note="Sectors: no fill when clear, orange 3–7 m, red under 3 m, distance inside. Fatigue three-step bar, seatbelt and tilt with words.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-2 gap-8">
                    <div className="max-w-[480px]">
                      <SafetyPanel {...sampleSafety} />
                    </div>
                    <div className="max-w-[480px]">
                      <SafetyPanel {...sampleSafetyClear} />
                    </div>
                  </div>
                )}
              />
            </Section>

            <Section id="twin" title="DigitalTwin" note="Five regions tinted by health (≥ 75% OK, 50–75% warning, under 50% critical). Tap a region or row for its signals.">
              <BothModes render={() => <DigitalTwin {...sampleTwin} className="min-h-[360px]" />} />
            </Section>

            <Section id="kpi" title="KpiTile" note="Plain numbers stay neutral; only a KPI that is a status gets a badge.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-4 gap-4">
                    <KpiTile label="Fleet utilisation" value="71" unit="%" delta={{ value: '+4', direction: 'up', caption: 'pts vs last week' }} />
                    <KpiTile label="Fuel used today" value="1,240" unit="L" />
                    <KpiTile label="Idle time" value="18" unit="%" delta={{ value: '−2', direction: 'down', caption: 'pts vs last week' }} />
                    <KpiTile label="Critical alerts open" value="1" status="critical" />
                  </div>
                )}
              />
            </Section>

            <Section id="empty" title="EmptyState" note="Say what will appear and how to get it. Loading uses skeleton blocks.">
              <BothModes
                render={() => (
                  <div className="grid grid-cols-3 gap-4">
                    <EmptyState icon={ClipboardList} title="No tasks yet." hint="Your supervisor will assign today's work." />
                    <EmptyState
                      icon={CloudOff}
                      title="Couldn't load tasks."
                      hint="Check connection and pull to refresh."
                      action={<Button variant="secondary">Try again</Button>}
                    />
                    <div className="flex flex-col gap-3" aria-label="Loading">
                      <Skeleton className="h-10 w-2/3" />
                      <Skeleton className="h-24" />
                      <Skeleton className="h-24" />
                    </div>
                  </div>
                )}
              />
            </Section>

            <Section id="offline" title="OfflineIndicator" note="Lives in the status bar. Queued writes show a clock and a count.">
              <BothModes
                render={() => (
                  <div className="flex flex-wrap gap-8">
                    <OfflineIndicator online />
                    <OfflineIndicator online={false} queued={3} />
                  </div>
                )}
              />
            </Section>

            <Section id="layouts" title="Layouts" note="Reference sizes. Scroll sideways inside each frame; the real routes are /operator and /manager.">
              <div className="flex flex-col gap-6">
                <div className="overflow-auto rounded-lg border">
                  <div className="h-[800px] w-[1280px]">
                    <CabLayout
                      embedded
                      status={sampleStatusBar}
                      primary={<TaskCard task={sampleTasks[0]!} position={1} />}
                      safety={<SafetyPanel {...sampleSafety} />}
                      onSos={() => undefined}
                    />
                  </div>
                </div>
                <div className="overflow-auto rounded-lg border">
                  <div className="h-[700px] w-[1440px]">
                    <OfficeLayout embedded title="Fleet" alertCount={{ open: 7, critical: 1 }}>
                      <KpiTile className="col-span-3" label="Machines active" value="9" unit="/ 12" />
                      <KpiTile className="col-span-3" label="Fleet utilisation" value="71" unit="%" />
                      <KpiTile className="col-span-3" label="Idle time" value="18" unit="%" />
                      <KpiTile className="col-span-3" label="Critical alerts open" value="1" status="critical" />
                      <div className="col-span-8 h-64 rounded-md border bg-surface" />
                      <div className="col-span-4 h-64 rounded-md border bg-surface" />
                    </OfficeLayout>
                  </div>
                </div>
              </div>
            </Section>
          </main>
        </div>
      </div>
    </ModeProvider>
  );
}
