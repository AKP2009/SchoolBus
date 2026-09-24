import { useEffect, useRef } from 'react';
import type { Alert } from '@/types/domain';

// Alert sounds (design.md §Sound and haptics): warning = one 440 Hz tone, 200 ms; critical =
// two-tone 880/660 Hz every 3 s until acknowledged; emergency = continuous alternating tone.
// Browsers only allow audio after a user gesture: unlockAudio() runs on the sign-in tap.

let ctx: AudioContext | null = null;

function audio(): AudioContext | null {
  if (typeof window === 'undefined' || !('AudioContext' in window)) return null;
  ctx ??= new AudioContext();
  return ctx;
}

export function unlockAudio(): void {
  void audio()?.resume();
}

function beep(freq: number, startAt: number, durS: number, gain = 0.2) {
  const a = audio();
  if (!a || a.state !== 'running') return;
  const osc = a.createOscillator();
  const g = a.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  // short ramps avoid clicks
  g.gain.setValueAtTime(0, startAt);
  g.gain.linearRampToValueAtTime(gain, startAt + 0.01);
  g.gain.setValueAtTime(gain, startAt + durS - 0.02);
  g.gain.linearRampToValueAtTime(0, startAt + durS);
  osc.connect(g).connect(a.destination);
  osc.start(startAt);
  osc.stop(startAt + durS);
}

export function playWarning(): void {
  const a = audio();
  if (a) beep(440, a.currentTime, 0.2);
}

function playCriticalOnce() {
  const a = audio();
  if (!a) return;
  beep(880, a.currentTime, 0.25);
  beep(660, a.currentTime + 0.3, 0.25);
}

/** Starts a repeating pattern; returns stop(). */
function loop(kind: 'critical' | 'emergency'): () => void {
  if (kind === 'critical') {
    playCriticalOnce();
    const id = window.setInterval(playCriticalOnce, 3000);
    return () => window.clearInterval(id);
  }
  let hi = true;
  const tick = () => {
    const a = audio();
    if (a) beep(hi ? 880 : 660, a.currentTime, 0.45, 0.18);
    hi = !hi;
  };
  tick();
  const id = window.setInterval(tick, 500);
  return () => window.clearInterval(id);
}

function speak(text: string) {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.95;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}

/**
 * Plays the right sound for the alert on screen. `top` = the takeover being shown (or null),
 * `banner` = the warning banner. Critical instructions are read aloud once; the tablet vibrates.
 */
export function useAlertSounds(top: Alert | null, banner: Alert | null): void {
  const spoken = useRef(new Set<string>());
  const warned = useRef(new Set<string>());

  const topKey = top ? `${top.id}:${top.stage}` : null;
  useEffect(() => {
    if (!top || (top.severity !== 'critical' && top.severity !== 'emergency')) return;
    const stop = loop(top.severity === 'emergency' ? 'emergency' : 'critical');
    if (topKey && !spoken.current.has(topKey)) {
      spoken.current.add(topKey);
      speak(`${top.title}. ${top.recommended_action ?? ''}`);
      navigator.vibrate?.([300, 150, 300]);
    }
    return stop;
    // restart the pattern when the alert or its stage changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topKey]);

  const bannerKey = banner ? `${banner.id}:${banner.stage}` : null;
  useEffect(() => {
    if (!banner || banner.severity !== 'warning' || !bannerKey || warned.current.has(bannerKey)) return;
    warned.current.add(bannerKey);
    playWarning();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bannerKey]);
}
