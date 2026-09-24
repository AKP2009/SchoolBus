import type { AlertStage, MachineType } from '@/types/domain';

// Takeover steps are UI-side (Alert.steps). They follow the graded response in docs/models.md and
// thresholds.yaml: never cut power, lower the attachment, reach level ground, cool down, then stop.

const ATTACHMENT: Record<MachineType, string> = {
  excavator: 'Lower the bucket to the ground',
  wheel_loader: 'Lower the bucket to the ground',
  dozer: 'Lower the blade to the ground',
  articulated_truck: 'Stop and apply the parking brake',
};

const SHUTDOWN_CODES = /^(COOLANT|HYD_OIL|OIL_PRESSURE|HYD_PRESSURE|FAULT_CODE)/;

export function stepsFor(alertCode: string, stage: AlertStage, machineType: MachineType | string | undefined): string[] | undefined {
  const first = ATTACHMENT[(machineType ?? 'excavator') as MachineType] ?? ATTACHMENT.excavator;
  if (SHUTDOWN_CODES.test(alertCode) && (stage === 'recommend_shutdown' || stage === 'escalated')) {
    if (alertCode.startsWith('OIL_PRESSURE')) return [first, 'Move to level ground', 'Shut down now. Do not idle'];
    if (alertCode.startsWith('HYD_PRESSURE')) return [first, 'Shut down', 'Do not restart until it is checked'];
    return [first, 'Move to level ground', 'Idle for 3 minutes to cool', 'Shut down'];
  }
  if (SHUTDOWN_CODES.test(alertCode) && stage === 'derate') return ['Switch to economy mode', 'Reduce the load'];
  if (alertCode === 'TIP_RISK') return ['Slow down', 'Keep the attachment low', 'Move to flatter ground'];
  if (alertCode.startsWith('PROXIMITY')) return ['Stop all movement', 'Sound the horn', 'Wait until you can see them clear'];
  if (alertCode === 'SEATBELT') return ['Stop the machine', 'Fasten your seatbelt'];
  return undefined;
}
