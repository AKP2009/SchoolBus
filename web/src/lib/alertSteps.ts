import { t, type Key } from '@/i18n';
import type { AlertStage, MachineType } from '@/types/domain';

// Takeover steps are UI-side (Alert.steps), in the cab's language. They follow the graded response in
// docs/models.md and thresholds.yaml: never cut power, lower the attachment, reach level ground, cool
// down, then stop.

const ATTACHMENT: Record<MachineType, Key> = {
  excavator: 'step.lowerBucket',
  wheel_loader: 'step.lowerBucket',
  dozer: 'step.lowerBlade',
  articulated_truck: 'step.parkingBrake',
};

const SHUTDOWN_CODES = /^(COOLANT|HYD_OIL|OIL_PRESSURE|HYD_PRESSURE|FAULT_CODE)/;

function keysFor(alertCode: string, stage: AlertStage, machineType: MachineType | string | undefined): Key[] | undefined {
  const first = ATTACHMENT[(machineType ?? 'excavator') as MachineType] ?? ATTACHMENT.excavator;
  if (SHUTDOWN_CODES.test(alertCode) && (stage === 'recommend_shutdown' || stage === 'escalated')) {
    if (alertCode.startsWith('OIL_PRESSURE')) return [first, 'step.levelGround', 'step.shutDownNow'];
    if (alertCode.startsWith('HYD_PRESSURE')) return [first, 'step.shutDown', 'step.noRestart'];
    return [first, 'step.levelGround', 'step.idleCool', 'step.shutDown'];
  }
  if (SHUTDOWN_CODES.test(alertCode) && stage === 'derate') return ['step.economy', 'step.reduceLoad'];
  if (alertCode === 'TIP_RISK') return ['step.slowDown', 'step.attachmentLow', 'step.flatterGround'];
  if (alertCode === 'EYES_CLOSED') return ['step.stopSafely', 'step.lowerAttachment', 'step.takeBreak'];
  if (alertCode.startsWith('PROXIMITY') || alertCode.startsWith('BLINDSPOT')) return ['step.stopMovement', 'step.horn', 'step.waitClear'];
  if (alertCode === 'SEATBELT') return ['step.stopMachine', 'step.fastenBelt'];
  return undefined;
}

export function stepsFor(alertCode: string, stage: AlertStage, machineType: MachineType | string | undefined): string[] | undefined {
  return keysFor(alertCode, stage, machineType)?.map((k) => t(k));
}
