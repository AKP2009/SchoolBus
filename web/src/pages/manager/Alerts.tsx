import { AlertsFeed } from './AlertsFeed';
import { ManagerShell } from './Shell';

export function ManagerAlerts() {
  return (
    <ManagerShell title="Alerts">
      <section className="col-span-8 flex flex-col gap-3" aria-label="Open alerts">
        <AlertsFeed />
      </section>
      <aside className="col-span-4 flex flex-col gap-3 rounded-md border bg-surface p-4 text-office-body" aria-label="How alerts escalate">
        <h2 className="text-office-h2">How alerts escalate</h2>
        <p className="text-ink-2">The system never cuts power on its own: the machine may be lifting a load or on a slope.</p>
        <ol className="flex list-decimal flex-col gap-2 pl-5">
          <li>Warn the operator with a sound and a banner</li>
          <li>Reduce power (economy mode)</li>
          <li>Advise a safe shutdown with steps</li>
          <li>Escalate to you if it lasts or is ignored</li>
        </ol>
        <p className="text-ink-2">Acknowledge an alert to show you have seen it. Resolve it once it is dealt with.</p>
      </aside>
    </ManagerShell>
  );
}
