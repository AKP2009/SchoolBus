import { Bell, HeartPulse, Map, Trophy } from 'lucide-react';
import { EmptyState } from '@/components/EmptyState';
import { KpiTile } from '@/components/KpiTile';
import { OfficeLayout } from '@/layouts/OfficeLayout';

// Wireframe from design.md §Office mode layout, with sample KPIs until the manager screens are built.
export function ManagerFleet() {
  return (
    <OfficeLayout title="Fleet" alertCount={{ open: 7, critical: 1 }}>
      <KpiTile className="col-span-3" label="Machines active" value="9" unit="/ 12" />
      <KpiTile className="col-span-3" label="Fleet utilisation" value="71" unit="%" delta={{ value: '+4', direction: 'up', caption: 'pts vs last week' }} />
      <KpiTile className="col-span-3" label="Idle time" value="18" unit="%" delta={{ value: '−2', direction: 'down', caption: 'pts vs last week' }} />
      <KpiTile className="col-span-3" label="Critical alerts open" value="1" status="critical" />
      <section className="col-span-8 min-h-[420px] rounded-md border bg-surface p-4">
        <EmptyState icon={Map} title="Fleet map" hint="Machines appear here, coloured by status, once telemetry replay starts." className="h-full" />
      </section>
      <section className="col-span-4 rounded-md border bg-surface p-4">
        <EmptyState icon={Bell} title="No live alerts." hint="New alerts from any machine on this site appear here." className="h-full" />
      </section>
      <section className="col-span-6 rounded-md border bg-surface p-4">
        <EmptyState icon={HeartPulse} title="Maintenance risk" hint="Machines ranked by failure chance appear after the first model run." />
      </section>
      <section className="col-span-6 rounded-md border bg-surface p-4">
        <EmptyState icon={Trophy} title="Efficiency ranking" hint="Operators and machines ranked by weekly metrics appear here." />
      </section>
    </OfficeLayout>
  );
}

export function ManagerPlaceholder({ title }: { title: string }) {
  return (
    <OfficeLayout title={title} alertCount={{ open: 7, critical: 1 }}>
      <section className="col-span-12 rounded-md border bg-surface p-4">
        <EmptyState title={`${title} is coming next.`} hint="This page is part of the next build phase (docs/roadmap.md)." />
      </section>
    </OfficeLayout>
  );
}
