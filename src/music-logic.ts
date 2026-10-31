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
  volume?: number; // громкость своего плеера 0–100 (ядро до 1.0.4 её не присылало)
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

function volumeOf(s: MusicSnap | null | undefined): number {
  const v = Number(s?.volume);
  return Number.isFinite(v) && s?.volume !== undefined ? Math.max(0, Math.min(100, Math.round(v))) : 100;
}

/** Громкость трека для <audio> (0–1) из состояния ядра. */
export function playerGain(s: MusicSnap | null | undefined): number {
  return volumeOf(s) / 100;
}

export interface BarView {
  visible: boolean;
  playing: boolean;
  artist: string;
  title: string;
  volume: number;
  canLouder: boolean;
  canQuieter: boolean;
}

/** Плеер в окне Аи: показываем, пока свой плеер играет или стоит на паузе. */
export function barView(s: MusicSnap | null | undefined): BarView {
  const live = !!s && (s.status === "playing" || s.status === "paused") && !!s.current;
  const volume = volumeOf(s);
  return {
    visible: live,
    playing: live && s!.status === "playing",
    artist: live ? s!.current!.artist || "" : "",
    title: live ? s!.current!.title || "" : "",
    volume,
    canLouder: volume < 100,
    canQuieter: volume > 0,
  };
}
