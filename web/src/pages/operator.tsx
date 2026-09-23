import { useState } from 'react';
import { ClipboardList, FileWarning, GraduationCap } from 'lucide-react';
import { AlertBanner } from '@/components/AlertBanner';
import { DigitalTwin } from '@/components/DigitalTwin';
import { EmptyState } from '@/components/EmptyState';
import { Gauge } from '@/components/Gauge';
import { SafetyPanel } from '@/components/SafetyPanel';
import { TaskCard } from '@/components/TaskCard';
import { CabLayout } from '@/layouts/CabLayout';
import { sampleSafety, sampleStatusBar, sampleTasks, sampleTwin, sampleWarning } from '@/mocks/sample';
import type { Task } from '@/types/domain';

// Screens run on sample data until Phase 4 wires Supabase and the WebSocket.

export function OperatorTasks() {
  const [tasks, setTasks] = useState<Task[]>(sampleTasks);
  const [warning, setWarning] = useState(true);
  const set = (id: string, status: Task['status']) =>
    setTasks((ts) => ts.map((t) => (t.task_id === id ? { ...t, status } : t)));
  const current = tasks.find((t) => t.status === 'in_progress') ?? tasks.find((t) => t.status === 'scheduled');
  const next = tasks.find((t) => t.status === 'scheduled' && t !== current);

  return (
    <CabLayout
      status={sampleStatusBar}
      primary={
        <div className="flex flex-col gap-4">
          {warning && <AlertBanner level="warning" {...sampleWarning} onDismiss={() => setWarning(false)} />}
          {current ? (
            <>
              <h1 className="text-cab-h1">Current task</h1>
              <TaskCard
                task={current}
                position={tasks.indexOf(current) + 1}
                onStart={(id) => set(id, 'in_progress')}
                onComplete={(id) => set(id, 'completed')}
              />
            </>
          ) : (
            <EmptyState
              icon={ClipboardList}
              title="No tasks yet."
              hint="Your supervisor will assign today's work."
            />
          )}
          {next && (
            <>
              <h2 className="text-cab-h2 text-ink-2">Next</h2>
              <TaskCard task={next} position={tasks.indexOf(next) + 1} />
            </>
          )}
        </div>
      }
      safety={<SafetyPanel {...sampleSafety} />}
    />
  );
}

export function OperatorMachine() {
  return (
    <CabLayout
      status={sampleStatusBar}
      primary={
        <div className="flex flex-col gap-6">
          <h1 className="text-cab-h1">Machine</h1>
          <DigitalTwin {...sampleTwin} />
          <div className="grid grid-cols-2 gap-6">
            <Gauge label="Coolant" value={96} unit="°C" min={40} max={120} normal={[75, 100]} trend="rising" />
            <Gauge
              label="Hydraulic oil"
              value={94}
              unit="°C"
              min={20}
              max={110}
              normal={[40, 85]}
              trend="rising"
            />
          </div>
        </div>
      }
      safety={<SafetyPanel {...sampleSafety} />}
    />
  );
}

export function OperatorPlaceholder({ page }: { page: 'training' | 'report' }) {
  return (
    <CabLayout
      status={sampleStatusBar}
      primary={
        page === 'training' ? (
          <EmptyState
            icon={GraduationCap}
            title="No training assigned yet."
            hint="Modules suggested for you will appear here after your first week."
          />
        ) : (
          <EmptyState
            icon={FileWarning}
            title="Report incident"
            hint="The incident form arrives in the next build. For now, hold SOS for an emergency."
          />
        )
      }
      safety={<SafetyPanel {...sampleSafety} />}
    />
  );
}
