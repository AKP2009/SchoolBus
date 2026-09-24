import { useState } from 'react';
import { Map as MapIcon, Plus, Save, Undo2, X } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { FleetMap, ZONE_WORD } from '@/components/FleetMap';
import { StatusBadge } from '@/components/StatusBadge';
import { saveGeofence, setGeofenceActive, useFleet } from '@/data/hooks';
import { cx } from '@/lib/cx';
import type { GeofenceType } from '@/types/domain';
import { ManagerShell, useSiteId } from './Shell';

const TYPES: GeofenceType[] = ['no_go', 'power_line', 'trench', 'pedestrian', 'speed_limited'];

const SWATCH: Record<GeofenceType, string> = {
  no_go: 'border-critical bg-tint-critical',
  power_line: 'border-critical bg-tint-critical',
  trench: 'border-critical bg-tint-critical',
  pedestrian: 'border-info bg-tint-info',
  speed_limited: 'border-dashed border-saffron-600',
};

const input = 'min-h-touch-office rounded-sm border border-line bg-surface px-2 text-office-body';

export function ManagerGeofences() {
  const siteId = useSiteId();
  const fleet = useFleet(siteId);
  const [draft, setDraft] = useState<Array<[number, number]> | null>(null);
  const [name, setName] = useState('');
  const [type, setType] = useState<GeofenceType>('no_go');
  const [limit, setLimit] = useState('10');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!siteId || !draft || draft.length < 3 || !name.trim()) return;
    setSaving(true);
    const ring = draft.map(([lat, lon]) => [lon, lat]);
    await saveGeofence(siteId, {
      site_id: siteId,
      name: name.trim(),
      zone_type: type,
      polygon: { type: 'Polygon', coordinates: [[...ring, ring[0]!]] },
      speed_limit_kmh: type === 'speed_limited' ? Number(limit) || null : null,
      active: true,
    });
    setSaving(false);
    setDraft(null);
    setName('');
  };

  return (
    <ManagerShell
      title="Geofences"
      actions={
        !draft && (
          <Button icon={Plus} onClick={() => setDraft([])}>
            Draw a zone
          </Button>
        )
      }
    >
      <DataState
        res={fleet}
        isEmpty={(f) => !f}
        errorTitle="Couldn't load the site map."
        empty={{ icon: MapIcon, title: 'No site map yet.', hint: 'The map appears once the site has machines.' }}
        className="col-span-12"
        skeleton={<div className="h-[560px] rounded-md bg-raised" />}
      >
        {(f) => (
          <>
            <section className="col-span-8 flex flex-col gap-2" aria-label="Site map">
              {draft && (
                <p className="rounded-md border border-info bg-tint-info px-3 py-2 text-office-body">
                  Click the map to add corners (<span className="reading">{draft.length}</span> so far, at least 3). Operators are warned when they enter the zone.
                </p>
              )}
              <FleetMap
                machines={f.machines}
                geofences={f.geofences}
                draft={draft ?? undefined}
                onMapClick={draft ? (lat, lon) => setDraft((d) => [...(d ?? []), [lat, lon]]) : undefined}
                className={cx('h-[560px]', draft && 'cursor-crosshair')}
              />
              <ul className="flex flex-wrap gap-4 text-office-small text-ink-2" aria-label="Legend">
                <li className="flex items-center gap-2">
                  <span className={cx('h-4 w-6 rounded-sm border-2', SWATCH.no_go)} /> No-go (hatched)
                </li>
                <li className="flex items-center gap-2">
                  <span className={cx('h-4 w-6 rounded-sm border-2', SWATCH.pedestrian)} /> Pedestrian zone
                </li>
                <li className="flex items-center gap-2">
                  <span className={cx('h-4 w-6 rounded-sm border-2', SWATCH.speed_limited)} /> Speed limit
                </li>
              </ul>
            </section>

            <section className="col-span-4 flex flex-col gap-3" aria-label="Zones">
              {draft && (
                <div className="flex flex-col gap-3 rounded-md border border-saffron-500 bg-surface p-4">
                  <h2 className="text-office-h2">New zone</h2>
                  <label className="flex flex-col gap-1 text-office-small text-ink-2">
                    Name
                    <input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Culvert works" />
                  </label>
                  <label className="flex flex-col gap-1 text-office-small text-ink-2">
                    Type
                    <select className={input} value={type} onChange={(e) => setType(e.target.value as GeofenceType)}>
                      {TYPES.map((t) => (
                        <option key={t} value={t}>
                          {ZONE_WORD[t]}
                        </option>
                      ))}
                    </select>
                  </label>
                  {type === 'speed_limited' && (
                    <label className="flex flex-col gap-1 text-office-small text-ink-2">
                      Speed limit (km/h)
                      <input className={cx(input, 'reading')} inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} />
                    </label>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button icon={Save} onClick={() => void save()} disabled={saving || draft.length < 3 || !name.trim()}>
                      Save zone
                    </Button>
                    <Button variant="secondary" icon={Undo2} onClick={() => setDraft((d) => (d ?? []).slice(0, -1))} disabled={draft.length === 0}>
                      Undo corner
                    </Button>
                    <Button variant="ghost" icon={X} onClick={() => setDraft(null)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
              {f.geofences.length === 0 ? (
                <p className="rounded-md border border-dashed p-4 text-office-body text-ink-2">No zones yet. Draw one around power lines, trenches or walkways.</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {f.geofences.map((g) => (
                    <li key={g.id} className="flex items-center justify-between gap-3 rounded-md border bg-surface p-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className={cx('h-5 w-7 shrink-0 rounded-sm border-2', SWATCH[g.zone_type])} aria-hidden />
                        <div className="min-w-0">
                          <p className="truncate text-office-h3">{g.name}</p>
                          <p className="text-office-small text-ink-2">
                            {ZONE_WORD[g.zone_type]}
                            {g.speed_limit_kmh != null && (
                              <>
                                {' '}
                                · <span className="reading">{g.speed_limit_kmh}</span> km/h
                              </>
                            )}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {g.active ? <StatusBadge status="ok" label="On" /> : <span className="rounded-sm border px-2 py-0.5 text-office-small text-ink-2">Off</span>}
                        <Button variant="secondary" onClick={() => siteId && void setGeofenceActive(siteId, g.id, !g.active)}>
                          {g.active ? 'Turn off' : 'Turn on'}
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </DataState>
    </ManagerShell>
  );
}
