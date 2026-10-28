/* Собственный плеер Аи: чистая логика без DOM (тестируется отдельно). */

export interface MusicItem {
  id: string;
  title: string;
  artist: string;
  cover?: string;
  duration?: number;
}

export interface MusicSnap {
  seq: number;
  cmd: string;
  status: "idle" | "playing" | "paused" | string;
  pos: number;
  count: number;
  current: MusicItem | null;
}

export type MusicStep = "none" | "start" | "pause" | "resume" | "stop";

/** Что делать окну, получив новое состояние из ядра. */
export function musicStep(prev: { seq: number; itemId: string; playing: boolean }, snap: MusicSnap | null | undefined): MusicStep {
  if (!snap || typeof snap.seq !== "number" || snap.seq === prev.seq) return "none";
  if (snap.status === "idle" || !snap.current) return prev.itemId || prev.playing ? "stop" : "none";
  if (snap.current.id !== prev.itemId) return snap.status === "playing" ? "start" : "none";
  if (snap.status === "paused") return prev.playing ? "pause" : "none";
  if (snap.status === "playing") return prev.playing ? "none" : "resume";
  return "none";
}

/** Плавная громкость: шаг к цели за тик, без щелчков. */
export function rampVolume(current: number, target: number, step = 0.08): number {
  if (Math.abs(target - current) <= step) return target;
  return current + Math.sign(target - current) * step;
}

export function nowPlayingLine(it: MusicItem | null): string {
  if (!it) return "";
  return "♪ " + [it.artist, it.title].filter(Boolean).join(" — ");
}
