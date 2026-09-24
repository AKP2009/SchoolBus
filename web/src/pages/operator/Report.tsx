import { useState, type FormEvent } from 'react';
import { CircleCheck, Clock, FileWarning, Mic, RotateCcw } from 'lucide-react';
import { Button } from '@/components/Button';
import { StatusBadge } from '@/components/StatusBadge';
import { isPending, reportIncident, useIncidents } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtTime } from '@/lib/format';
import { useConnection } from '@/stores/connection';
import type { IncidentRow, IncidentType, SeverityLevel } from '@/types/domain';
import { OperatorShell, useCab } from './Shell';
import { useT, type Key } from '@/i18n';

const TYPES: IncidentType[] = ['near_miss', 'collision', 'injury', 'equipment_damage', 'spill_leak', 'other'];
const SEVERITIES: Array<{ v: SeverityLevel; label: Key }> = [
  { v: 'info', label: 'report.minor' },
  { v: 'warning', label: 'report.serious' },
  { v: 'critical', label: 'report.critical' },
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
  const t = useT();
  const cab = useCab();
  const online = useConnection((s) => s.online);
  useConnection((s) => s.queued); // re-render as the offline queue drains
  const incidents = useIncidents(cab?.siteId);
  const [type, setType] = useState<IncidentType | null>(null);
  const [severity, setSeverity] = useState<SeverityLevel>('warning');
  const [injury, setInjury] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ row: IncidentRow; queued: boolean } | null>(null);
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
        <h1 className="text-cab-h1">{t('report.title')}</h1>
        <Button variant="secondary" icon={Mic} onClick={() => setVoiceNote(true)}>
          {t('report.speak')}
        </Button>
      </div>
      {voiceNote && (
        <p role="status" className="rounded-md border border-info bg-tint-info px-4 py-3 text-cab-body">
          {t('report.voiceNote')}
        </p>
      )}

      {done && (
        <section role="status" aria-live="polite" className="flex items-center justify-between gap-4 rounded-md border border-ok bg-tint-ok px-6 py-4">
          <p className="flex items-center gap-3 text-cab-h2">
            {done.queued ? <Clock size={32} aria-hidden /> : <CircleCheck size={32} className="text-ok" aria-hidden />}
            {done.queued ? t('report.savedQueued') : t('report.sent')}
          </p>
          <Button variant="ghost" icon={RotateCcw} onClick={() => setDone(null)}>
            {t('report.another')}
          </Button>
        </section>
      )}

      {!done && (
        <form onSubmit={submit} className="flex flex-col gap-5" aria-label={t('report.formAria')}>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-cab-small text-ink-2">{t('report.what')}</legend>
            <div className="grid grid-cols-3 gap-3">
              {TYPES.map((k) => (
                <Choice key={k} selected={type === k} onClick={() => setType(k)}>
                  {t(`incident.${k}`)}
                </Choice>
              ))}
            </div>
          </fieldset>
          <div className="grid grid-cols-[1fr_auto] gap-6">
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-cab-small text-ink-2">{t('report.howSerious')}</legend>
              <div className="grid grid-cols-3 gap-3">
                {SEVERITIES.map((s) => (
                  <Choice key={s.v} selected={!injury && severity === s.v} onClick={() => setSeverity(s.v)}>
                    {t(s.label)}
                  </Choice>
                ))}
              </div>
            </fieldset>
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-cab-small text-ink-2">{t('report.hurt')}</legend>
              <div className="grid grid-cols-2 gap-3">
                <Choice selected={!injury} onClick={() => setInjury(false)}>
                  {t('report.no')}
                </Choice>
                <Choice selected={injury} onClick={() => setInjury(true)}>
                  {t('report.yes')}
                </Choice>
              </div>
            </fieldset>
          </div>
          <label className="flex flex-col gap-2">
            <span className="text-cab-small text-ink-2">{t('report.describe')}</span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={2}
              placeholder={t('report.placeholder')}
              className="rounded-md border border-line bg-raised px-4 py-3 text-cab-body text-ink placeholder:text-ink-3 focus:border-saffron-500"
            />
          </label>
          <div className="flex items-center gap-6">
            <Button type="submit" icon={FileWarning} disabled={busy || !type || !text.trim()}>
              {t('report.submit')}
            </Button>
            {!online && <span className="text-cab-small text-ink-2">{t('report.offline')}</span>}
          </div>
        </form>
      )}

      {mine.length > 0 && (
        <section className="flex flex-col gap-2" aria-label={t('report.recent')}>
          <h2 className="text-cab-h2 text-ink-2">{t('report.recent')}</h2>
          <ul className="flex flex-col divide-y divide-line rounded-md border bg-surface">
            {mine.map((i) => (
              <li key={i.client_id} className="flex items-center justify-between gap-4 px-6 py-3 text-cab-body">
                <span className="min-w-0 truncate">
                  <span className="reading mr-3 text-ink-2">{fmtTime(i.ts)}</span>
                  {t(`incident.${i.incident_type}`)} · {i.description}
                </span>
                {isPending(i.client_id) ? (
                  <span className="flex shrink-0 items-center gap-2 text-cab-small text-ink-2">
                    <Clock size={24} aria-hidden /> {t('report.waitingSync')}
                  </span>
                ) : (
                  <StatusBadge status="ok" label={t('report.sent')} className="shrink-0" />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </OperatorShell>
  );
}
