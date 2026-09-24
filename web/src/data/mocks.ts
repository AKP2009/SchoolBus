// Loads web/src/mocks/*.json lazily (own chunk, only fetched in mock mode). The files are built by
// scripts/build_mocks.py from the loaded data; each matches a shape in docs/api_contract.md.
import type {
  AlertRow,
  ChatExamples,
  Clusters,
  FatigueRow,
  Fleet,
  Handover,
  IncidentRow,
  MachineHealth,
  MachineLogs,
  MachineSignals,
  MaintenancePrediction,
  PlanResult,
  SafetyEventRow,
  ShiftRow,
  TaskPrediction,
  TaskRow,
  Telemetry,
  TelemetryStream,
  Training,
  World,
} from '@/types/domain';

export interface MockFiles {
  world: World;
  tasks: TaskRow[];
  task_predictions: { predictions: TaskPrediction[] };
  handover: Handover;
  machine_health: MachineHealth[];
  machine_live: Telemetry;
  machine_signals: MachineSignals;
  machine_logs: MachineLogs;
  alerts_open: AlertRow[];
  safety_events: SafetyEventRow[];
  shifts: ShiftRow[];
  fatigue: FatigueRow[];
  incidents: IncidentRow[];
  fleet: Fleet;
  maintenance_predictions: MaintenancePrediction[];
  clusters: Clusters;
  training: Training;
  chat: ChatExamples;
  plan: PlanResult;
  telemetry_stream: TelemetryStream;
}

type Loader = () => Promise<{ default: unknown }>;

const LOADERS: { [K in keyof MockFiles]: Loader } = {
  world: () => import('@/mocks/world.json'),
  tasks: () => import('@/mocks/tasks.json'),
  task_predictions: () => import('@/mocks/task_predictions.json'),
  handover: () => import('@/mocks/handover.json'),
  machine_health: () => import('@/mocks/machine_health.json'),
  machine_live: () => import('@/mocks/machine_live.json'),
  machine_signals: () => import('@/mocks/machine_signals.json'),
  machine_logs: () => import('@/mocks/machine_logs.json'),
  alerts_open: () => import('@/mocks/alerts_open.json'),
  safety_events: () => import('@/mocks/safety_events.json'),
  shifts: () => import('@/mocks/shifts.json'),
  fatigue: () => import('@/mocks/fatigue.json'),
  incidents: () => import('@/mocks/incidents.json'),
  fleet: () => import('@/mocks/fleet.json'),
  maintenance_predictions: () => import('@/mocks/maintenance_predictions.json'),
  clusters: () => import('@/mocks/clusters.json'),
  training: () => import('@/mocks/training.json'),
  chat: () => import('@/mocks/chat.json'),
  plan: () => import('@/mocks/plan.json'),
  telemetry_stream: () => import('@/mocks/telemetry_stream.json'),
};

const cache = new Map<keyof MockFiles, Promise<unknown>>();

/** A deep copy each time, so screens can't mutate the shared mock. */
export async function mock<K extends keyof MockFiles>(name: K): Promise<MockFiles[K]> {
  let p = cache.get(name);
  if (!p) {
    p = LOADERS[name]().then((m) => m.default);
    cache.set(name, p);
  }
  return structuredClone((await p) as MockFiles[K]);
}
