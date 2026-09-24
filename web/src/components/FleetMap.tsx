import { useEffect, useMemo, type ReactNode } from 'react';
import L from 'leaflet';
import { MapContainer, Marker, Polygon, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import { cx } from '@/lib/cx';
import { healthStatus, STATUS, type Status } from '@/lib/status';
import type { FleetMachine, Geofence, GeofenceType, MachineType } from '@/types/domain';

// design.md §Maps: muted basemap (OSM tiles desaturated in CSS: CARTO now needs an API key), machines as circles with a type glyph and a status ring,
// no-go zones red hatch, pedestrian blue outline, speed zones dashed saffron outline.
// Colours come from CSS classes in index.css (Leaflet writes SVG attributes, which can't read variables).

const GLYPH: Record<MachineType, string> = { excavator: 'EX', wheel_loader: 'WL', dozer: 'DZ', articulated_truck: 'AT' };

export const ZONE_CLASS: Record<GeofenceType, string> = {
  no_go: 'gf-nogo',
  power_line: 'gf-nogo',
  trench: 'gf-nogo',
  pedestrian: 'gf-pedestrian',
  speed_limited: 'gf-speed',
};

export const ZONE_WORD: Record<GeofenceType, string> = {
  no_go: 'No-go',
  power_line: 'Power line (no-go)',
  trench: 'Trench (no-go)',
  pedestrian: 'Pedestrian zone',
  speed_limited: 'Speed limit',
};

/** Map status of a machine: its worst open alert, else its health band; off shift = offline. */
export function machineStatus(m: FleetMachine): Status {
  if (m.worst_severity === 'emergency' || m.worst_severity === 'critical') return 'critical';
  if (m.worst_severity === 'warning') return 'warning';
  if (!m.live.on_shift) return 'offline';
  return healthStatus(m.health_overall);
}

const RING: Record<Status, string> = {
  ok: 'border-ok',
  info: 'border-info',
  warning: 'border-warning',
  critical: 'border-critical',
  emergency: 'border-critical',
  offline: 'border-offline',
  unknown: 'border-offline',
};

function icon(m: FleetMachine, selected: boolean) {
  const s = machineStatus(m);
  return L.divIcon({
    className: '',
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    html: `<div class="${cx(
      'flex h-11 w-11 items-center justify-center rounded-full border-4 bg-surface font-mono text-office-small font-medium text-ink',
      RING[s],
      selected && 'outline outline-4 outline-offset-2 outline-saffron-500',
    )}" aria-label="${m.machine_id} ${STATUS[s].word}">${GLYPH[m.machine_type]}</div>`,
  });
}

function FitBounds({ points }: { points: Array<[number, number]> }) {
  const map = useMap();
  const key = points.map((p) => p.join(',')).join(';');
  useEffect(() => {
    if (points.length === 0) return;
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 17 });
    // fit once per set of points
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);
  return null;
}

function ClickCapture({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onClick(e.latlng.lat, e.latlng.lng) });
  return null;
}

export interface FleetMapProps {
  machines: FleetMachine[];
  geofences?: Geofence[];
  selectedId?: string | null;
  onSelect?: (machineId: string) => void;
  /** Geofence drawing: clicks on the map add points. */
  onMapClick?: (lat: number, lon: number) => void;
  draft?: Array<[number, number]>;
  center?: [number, number];
  className?: string;
  children?: ReactNode;
}

export function FleetMap({ machines, geofences = [], selectedId, onSelect, onMapClick, draft, center, className, children }: FleetMapProps) {
  const placed = machines.filter((m) => m.live.gps_lat != null && m.live.gps_lon != null);
  const points = useMemo<Array<[number, number]>>(
    () => [
      ...placed.map((m) => [m.live.gps_lat!, m.live.gps_lon!] as [number, number]),
      ...geofences.flatMap((g) => g.polygon.coordinates[0]!.map(([lon, lat]) => [lat!, lon!] as [number, number])),
    ],
    [placed, geofences],
  );
  return (
    <div className={cx('relative isolate overflow-hidden rounded-md border', className)}>
      {/* hatch for no-go zones, referenced by .gf-nogo in index.css */}
      <svg width="0" height="0" className="absolute" aria-hidden>
        <defs>
          <pattern id="gf-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="10" height="10" className="fill-tint-critical" />
            <line x1="0" y1="0" x2="0" y2="10" className="stroke-critical" strokeWidth="3" />
          </pattern>
        </defs>
      </svg>
      <MapContainer center={center ?? points[0] ?? [12.9165, 79.1325]} zoom={16} className="h-full w-full" scrollWheelZoom attributionControl>
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
          className="basemap-muted"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        <FitBounds points={points} />
        {geofences.map((g) => (
          <Polygon
            key={g.id}
            positions={g.polygon.coordinates[0]!.map(([lon, lat]) => [lat!, lon!] as [number, number])}
            pathOptions={{ className: cx(ZONE_CLASS[g.zone_type], !g.active && 'gf-inactive'), weight: 2 }}
          >
            <Tooltip sticky>
              {g.name} · {ZONE_WORD[g.zone_type]}
              {g.speed_limit_kmh != null && ` ${g.speed_limit_kmh} km/h`}
              {!g.active && ' (off)'}
            </Tooltip>
          </Polygon>
        ))}
        {draft && draft.length > 0 && <Polygon positions={draft} pathOptions={{ className: 'gf-draft', weight: 2 }} />}
        {placed.map((m) => (
          <Marker
            key={m.machine_id}
            position={[m.live.gps_lat!, m.live.gps_lon!]}
            icon={icon(m, m.machine_id === selectedId)}
            eventHandlers={{ click: () => onSelect?.(m.machine_id) }}
            keyboard
            title={`${m.machine_id} — ${STATUS[machineStatus(m)].word}`}
          >
            <Tooltip direction="top" offset={[0, -22]}>
              <span className="font-mono">{m.machine_id}</span> · {STATUS[machineStatus(m)].word}
              {m.live.operator_name && ` · ${m.live.operator_name}`}
            </Tooltip>
          </Marker>
        ))}
        {onMapClick && <ClickCapture onClick={onMapClick} />}
        {children}
      </MapContainer>
    </div>
  );
}
