/* Чистая логика взаимодействия с аватаром — без PIXI/Tauri, покрыта тестами. */

export type Mood = "calm" | "joyful" | "thoughtful" | "sad" | "surprise";
export type Zone = "head" | "body";

/** Нажатие: клик или перетаскивание окна. Порог в пикселях, чтобы дрожь руки
 *  не превращала клик в drag (раньше любое нажатие сразу таскало окно). */
export class PressGesture {
  private x = 0;
  private y = 0;
  private t = 0;
  active = false;
  dragged = false;

  constructor(private threshold = 5, private holdMs = 450) {}

  down(x: number, y: number, now: number) {
    this.x = x;
    this.y = y;
    this.t = now;
    this.active = true;
    this.dragged = false;
  }

  /** true — пора начинать перетаскивание окна. */
  move(x: number, y: number): boolean {
    if (!this.active || this.dragged) return false;
    if (Math.hypot(x - this.x, y - this.y) >= this.threshold) {
      this.dragged = true;
      return true;
    }
    return false;
  }

  /** Итог нажатия: tap | long | none (было перетаскивание). */
  up(now: number): "tap" | "long" | "none" {
    if (!this.active) return "none";
    this.active = false;
    if (this.dragged) return "none";
    return now - this.t >= this.holdMs ? "long" : "tap";
  }
}

/** «Поглаживание»: курсор водят туда-сюда над головой (без нажатия). */
export class RubDetector {
  private lastX: number | null = null;
  private dir = 0;
  private flips: number[] = [];

  constructor(private needFlips = 4, private windowMs = 1400, private minStep = 6) {}

  /** true — засчитано поглаживание (после этого счётчик сбрасывается). */
  feed(x: number, now: number): boolean {
    if (this.lastX === null) {
      this.lastX = x;
      return false;
    }
    const dx = x - this.lastX;
    if (Math.abs(dx) < this.minStep) return false;
    this.lastX = x;
    const d = Math.sign(dx);
    if (this.dir !== 0 && d !== this.dir) this.flips.push(now);
    this.dir = d;
    this.flips = this.flips.filter((t) => now - t <= this.windowMs);
    if (this.flips.length >= this.needFlips) {
      this.reset();
      return true;
    }
    return false;
  }

  reset() {
    this.lastX = null;
    this.dir = 0;
    this.flips = [];
  }
}

/** Голова или тело по вертикальной доле внутри рамки модели. */
export function zoneOf(yRatio: number, hitNames: string[] = []): Zone {
  const names = hitNames.map((n) => n.toLowerCase());
  if (names.some((n) => n.includes("head") || n.includes("face"))) return "head";
  if (names.some((n) => n.includes("body"))) return "body";
  return yRatio < 0.3 ? "head" : "body";
}

/** Выражение лица под настроение: по именам выражений модели (smile/sad/…)
 *  либо по известной раскладке Haru (F01…F08). */
export function pickExpression(mood: Mood | string, names: string[]): string | null {
  if (!names.length) return null;
  const lower = names.map((n) => n.toLowerCase());
  const want: Record<string, string[]> = {
    joyful: ["happy", "smile", "joy", "laugh", "笑"],
    sad: ["sad", "cry", "悲"],
    surprise: ["surpris", "shock", "驚"],
    thoughtful: ["think", "doubt", "confus", "考"],
    calm: ["normal", "default", "neutral", "idle"],
  };
  for (const key of want[mood] ?? []) {
    const i = lower.findIndex((n) => n.includes(key));
    if (i >= 0) return names[i];
  }
  const haru: Record<string, string> = { joyful: "F01", calm: "F02", thoughtful: "F03", sad: "F04", surprise: "F05" };
  const h = haru[mood];
  if (h && names.includes(h)) return h;
  return null;
}

/** Группа движений по смыслу: имена групп у моделей разные (TapBody, Tap, Tap@Body…). */
export function pickMotionGroup(kind: "tap" | "idle" | "flick", groups: string[]): string | null {
  const re = kind === "tap" ? /^tap/i : kind === "idle" ? /idle/i : /flick|shake/i;
  const found = groups.filter((g) => re.test(g));
  if (!found.length) return null;
  if (kind === "tap") return found.find((g) => /body/i.test(g)) ?? found[0];
  return found[0];
}

/** Надстройка параметров лица для моделей без выражений (стандартные ID Cubism). */
export function moodParams(mood: Mood | string): Record<string, number> {
  switch (mood) {
    case "joyful":
      return { ParamMouthForm: 0.8, ParamEyeLSmile: 0.6, ParamEyeRSmile: 0.6, ParamBrowLY: 0.2, ParamBrowRY: 0.2 };
    case "sad":
      return { ParamMouthForm: -0.6, ParamBrowLY: -0.5, ParamBrowRY: -0.5, ParamBrowLAngle: -0.5, ParamBrowRAngle: -0.5 };
    case "surprise":
      return { ParamBrowLY: 0.8, ParamBrowRY: 0.8, ParamEyeLOpen: 1.25, ParamEyeROpen: 1.25 };
    case "thoughtful":
      return { ParamBrowLY: -0.15, ParamBrowRY: 0.25, ParamMouthForm: -0.1 };
    default:
      return {};
  }
}

/** Плавное приближение (экспоненциальное сглаживание с учётом dt). */
export function approach(current: number, target: number, dtMs: number, halfLifeMs = 120): number {
  const k = 1 - Math.pow(0.5, dtMs / Math.max(1, halfLifeMs));
  return current + (target - current) * k;
}

/** Взгляд: курсор (CSS px окна, может быть за пределами) → focus модели в [-1,1]. */
export function gazeFromCursor(
  cx: number,
  cy: number,
  headX: number,
  headY: number,
  reach = 520,
): { x: number; y: number } {
  const clamp = (v: number) => Math.max(-1, Math.min(1, v));
  return { x: clamp((cx - headX) / reach), y: clamp((headY - cy) / reach) };
}

/** Уровень громкости для губ из байтов анализатора (0..1, с порогом шума). */
export function mouthLevel(bytes: Uint8Array, gain = 2.6, floor = 0.04): number {
  if (!bytes.length) return 0;
  let sum = 0;
  for (let i = 0; i < bytes.length; i++) {
    const v = (bytes[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / bytes.length);
  return Math.max(0, Math.min(1, (rms - floor) * gain));
}

/** Масштаб аватара в допустимых пределах. */
export function clampScale(v: number): number {
  if (!Number.isFinite(v)) return 1;
  return Math.max(0.5, Math.min(1.6, Math.round(v * 100) / 100));
}
