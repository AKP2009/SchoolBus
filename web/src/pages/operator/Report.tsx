import { useState, type FormEvent } from 'react';
import { CircleCheck, Clock, FileWarning, Mic, RotateCcw } from 'lucide-react';
import { Button } from '@/components/Button';
import { StatusBadge } from '@/components/StatusBadge';
import { reportIncident, useIncidents } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtTime, INCIDENT_WORD } from '@/lib/format';
import { useConnection } from '@/stores/connection';
import type { IncidentRow, IncidentType, SeverityLevel } from '@/types/domain';
import { OperatorShell, useCab } from './Shell';

const TYPES: IncidentType[] = ['near_miss', 'collision', 'injury', 'equipment_damage', 'spill_leak', 'other'];
const SEVERITIES: Array<{ v: SeverityLevel; label: string }> = [
  { v: 'info', label: 'Minor' },
  { v: 'warning', label: 'Serious' },
  { v: 'critical', label: 'Critical' },
];

function Choice({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cx(
        'min-h-touch-cab rounded-md border px-4 text-cab-body transition-state',
        selected ? 'border-saffron-500 bg-saffron-500 text-on-saffron' : 'border-line bg-raised text-ink hover:border-ink-3',
      )}
    >
      {children}
    </button>
  );
}

export function OperatorReport() {
  const cab = useCab();
  const online = useConnection((s) => s.online);
  const incidents = useIncidents(cab?.siteId);
  const [type, setType] = useState<IncidentType | null>(null);
  const [severity, setSeverity] = useState<SeverityLevel>('warning');
  const [injury, setInjury] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ row: IncidentRow; queued: boolean } | null>(null);
  const [queuedIds, setQueuedIds] = useState<Set<string>>(new Set());
  const [voiceNote, setVoiceNote] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!cab || !type || !text.trim()) return;
    setBusy(true);
    try {
      const res = await reportIncident({
        incident_type: type,
        severity: injury ? 'critical' : severity,
        description: text.trim(),
        injury,
        machine_id: cab.machineId,
        operator_id: cab.operatorId,
        site_id: cab.siteId,
      });
      if (res.queued) setQueuedIds((s) => new Set(s).add(res.row.client_id));
      setDone(res);
      setType(null);
      setText('');
      setInjury(false);
    } finally {
      setBusy(false);
    }
  };

  const mine = (incidents.data ?? []).filter((i) => i.operator_id === cab?.operatorId).slice(0, 3);

  return (
    <OperatorShell>
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-cab-h1">Report incident</h1>
        <Button variant="secondary" icon={Mic} onClick={() => setVoiceNote(true)}>
          Speak instead
        </Button>
      </div>
      {voiceNote && (
        <p role="status" className="rounded-md border border-info bg-tint-info px-4 py-3 text-cab-body">
          Voice reports need the voice service, which isn't connected yet. Fill in the form: it takes under 30 seconds.
        </p>
      )}

      {done && (
        <section role="status" aria-live="polite" className="flex items-center justify-between gap-4 rounded-md border border-ok bg-tint-ok px-6 py-4">
          <p className="flex items-center gap-3 text-cab-h2">
            {done.queued ? <Clock size={32} aria-hidden /> : <CircleCheck size={32} className="text-ok" aria-hidden />}
            {done.queued ? 'Incident saved — it will send when you are back online' : 'Incident reported'}
          </p>
          <Button variant="ghost" icon={RotateCcw} onClick={() => setDone(null)}>
            Report another
          </Button>
        </section>
      )}

      {!done && (
        <form onSubmit={submit} className="flex flex-col gap-5" aria-label="Incident form">
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-cab-small text-ink-2">What happened?</legend>
            <div className="grid grid-cols-3 gap-3">
              {TYPES.map((t) => (
                <Choice key={t} selected={type === t} onClick={() => setType(t)}>
                  {INCIDENT_WORD[t]}
                </Choice>
              ))}
            </div>
          </fieldset>
          <div className="grid grid-cols-[1fr_auto] gap-6">
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-cab-small text-ink-2">How serious?</legend>
              <div className="grid grid-cols-3 gap-3">
                {SEVERITIES.map((s) => (
                  <Choice key={s.v} selected={!injury && severity === s.v} onClick={() => setSeverity(s.v)}>
                    {s.label}
                  </Choice>
                ))}
              </div>
            </fieldset>
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-cab-small text-ink-2">Anyone hurt?</legend>
              <div className="grid grid-cols-2 gap-3">
                <Choice selected={!injury} onClick={() => setInjury(false)}>
                  No
                </Choice>
                <Choice selected={injury} onClick={() => setInjury(true)}>
                  Yes
                </Choice>
              </div>
            </fieldset>
          </div>
          <label className="flex flex-col gap-2">
            <span className="text-cab-small text-ink-2">Describe it in a few words</span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={2}
              placeholder="Worker walked behind the machine while I was reversing"
              className="rounded-md border border-line bg-raised px-4 py-3 text-cab-body text-ink placeholder:text-ink-3 focus:border-saffron-500"
            />
          </label>
          <div className="flex items-center gap-6">
            <Button type="submit" icon={FileWarning} disabled={busy || !type || !text.trim()}>
              Report incident
            </Button>
            {!online && <span className="text-cab-small text-ink-2">Offline — it will send when you are back online.</span>}
          </div>
        </form>
      )}

      {mine.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Your recent reports">
          <h2 className="text-cab-h2 text-ink-2">Your recent reports</h2>
          <ul className="flex flex-col divide-y divide-line rounded-md border bg-surface">
            {mine.map((i) => (
              <li key={i.client_id} className="flex items-center justify-between gap-4 px-6 py-3 text-cab-body">
                <span className="min-w-0 truncate">
                  <span className="reading mr-3 text-ink-2">{fmtTime(i.ts)}</span>
                  {INCIDENT_WORD[i.incident_type]} · {i.description}
                </span>
                {queuedIds.has(i.client_id) && !online ? (
                  <span className="flex shrink-0 items-center gap-2 text-cab-small text-ink-2">
                    <Clock size={24} aria-hidden /> Waiting to sync
                  </span>
                ) : (
                  <StatusBadge status="ok" label="Incident reported" className="shrink-0" />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </OperatorShell>
  );
}
