import { useEffect } from 'react';
import { setLanguage, type Lang } from '@/i18n';
import { useAlerts } from '@/stores/alerts';
import { useConnection } from '@/stores/connection';
import type { StreamMessage } from '@/types/domain';
import { mockReplay } from './live';

/**
 * The demo panel runs in a hidden tab; commands reach the operator and manager tabs over a
 * BroadcastChannel (same origin). Every tab also applies its own commands.
 */
export type DemoCommand =
  | { type: 'seek'; t: number }
  | { type: 'speed'; speed: number }
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'inject'; items: Array<StreamMessage & { offsetS: number }> }
  | { type: 'sos' }
  | { type: 'offline'; on: boolean }
  | { type: 'lang'; lang: Lang }
  | { type: 'reset' };

const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('cat-demo');

function apply(cmd: DemoCommand) {
  switch (cmd.type) {
    case 'seek':
      mockReplay.seek(cmd.t);
      useAlerts.getState().reset();
      break;
    case 'speed':
      mockReplay.setSpeed(cmd.speed);
      break;
    case 'play':
      mockReplay.play();
      break;
    case 'pause':
      mockReplay.pause();
      break;
    case 'inject':
      mockReplay.inject(cmd.items);
      break;
    case 'sos':
      useAlerts.getState().push({
        id: -Date.now(),
        ts: new Date().toISOString(),
        machine_id: null,
        title: 'SOS sent',
        message: 'Your location and machine state were sent to the site manager.',
        recommended_action: 'Stay in the cab if it is safe. Help is on the way.',
        severity: 'emergency',
        stage: 'escalated',
      });
      break;
    case 'offline':
      useConnection.getState().setForcedOffline(cmd.on);
      break;
    case 'lang':
      setLanguage(cmd.lang);
      break;
    case 'reset':
      useAlerts.setState({ local: [], acked: {}, dismissed: {} });
      useConnection.getState().setForcedOffline(false);
      break;
  }
}

export function sendDemo(cmd: DemoCommand): void {
  apply(cmd);
  channel?.postMessage(cmd);
}

/** Mount once at the app root so every tab obeys the demo panel. */
export function useDemoBus(): void {
  useEffect(() => {
    if (!channel) return;
    const on = (e: MessageEvent<DemoCommand>) => apply(e.data);
    channel.addEventListener('message', on);
    return () => channel.removeEventListener('message', on);
  }, []);
}
