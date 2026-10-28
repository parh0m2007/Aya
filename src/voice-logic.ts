/* Логика голосового ввода без браузерных API: конец фразы и обращение по имени. */

/** Уровень речи из байтов анализатора: только полоса голоса 300–3400 Гц
 *  (гул вентилятора и шипение не мешают), 0..1. */
export function speechLevel(bytes: Uint8Array, sampleRate: number, fftSize: number): number {
  const hz = sampleRate / fftSize;
  const lo = Math.max(1, Math.floor(300 / hz));
  const hi = Math.min(bytes.length - 1, Math.ceil(3400 / hz));
  if (hi <= lo) return 0;
  let sum = 0;
  for (let i = lo; i <= hi; i++) sum += bytes[i] * bytes[i];
  return Math.sqrt(sum / (hi - lo + 1)) / 255;
}

export interface EndpointOptions {
  startMs: number; // сколько держится голос, чтобы считать началом речи
  endMs: number; // тишина после речи = конец фразы
  minSpeechMs: number; // короче — это щелчок/кашель, не фраза
  absMin: number; // абсолютный порог уровня
  ratio: number; // во сколько раз громче фонового шума
}

export const DEFAULT_ENDPOINT: EndpointOptions = { startMs: 90, endMs: 650, minSpeechMs: 250, absMin: 0.045, ratio: 2.4 };

/** Детектор начала и конца фразы с адаптивным уровнем шума.
 *  Раньше конец фразы ловился фиксированным порогом за 12 тиков по 120 мс
 *  (1.44 с тишины); теперь ~0.65 с, а порог подстраивается под комнату. */
export class Endpointer {
  floor = 0.015;
  speaking = false;
  voicedMs = 0;
  silentMs = 0;
  private loudMs = 0;
  private calibMs = 0;
  private o: EndpointOptions;

  constructor(o: Partial<EndpointOptions> = {}) {
    this.o = { ...DEFAULT_ENDPOINT, ...o };
  }

  threshold(): number {
    return Math.max(this.o.absMin, this.floor * this.o.ratio);
  }

  /** Запись уже идёт (кнопка, окно ожидания) — ждём только конец фразы. */
  begin() {
    this.calibMs = Math.max(this.calibMs, 300); // запись уже идёт — калибровать некогда
    this.speaking = true;
    this.voicedMs = 0;
    this.silentMs = 0;
  }

  reset() {
    this.speaking = false;
    this.voicedMs = 0;
    this.silentMs = 0;
    this.loudMs = 0;
  }

  /** Новый поток микрофона — заново слушаем фон комнаты. */
  recalibrate() {
    this.reset();
    this.calibMs = 0;
  }

  /** "start" — пошла речь; "end" — фраза закончилась; null — ничего. */
  feed(level: number, dtMs: number): "start" | "end" | null {
    // первые ~300 мс — калибровка: просто слушаем фон
    if (this.calibMs < 300) {
      this.calibMs += dtMs;
      this.floor += (level - this.floor) * 0.25;
      return null;
    }
    const th = this.threshold();
    if (!this.speaking) {
      if (level > th) {
        // ровный громкий фон (вентилятор, музыка) тоже понемногу поднимает порог
        this.floor += (level - this.floor) * 0.004;
        this.loudMs += dtMs;
        if (this.loudMs >= this.o.startMs) {
          this.speaking = true;
          this.voicedMs = this.loudMs;
          this.silentMs = 0;
          this.loudMs = 0;
          return "start";
        }
      } else {
        this.loudMs = 0;
        // фон учим только в тишине — медленно вверх, быстрее вниз
        const k = level > this.floor ? 0.02 : 0.08;
        this.floor += (level - this.floor) * k;
      }
      return null;
    }
    // гистерезис: продолжать речь легче, чем начать — не рвём слова на паузах
    if (level > th * 0.75) {
      this.voicedMs += dtMs;
      this.silentMs = 0;
    } else {
      this.silentMs += dtMs;
    }
    if (this.silentMs >= this.o.endMs && this.voicedMs >= this.o.minSpeechMs) {
      this.reset();
      return "end";
    }
    return null;
  }
}

/* ── «Ая, …» ── */

const FILLERS = new Set(["эй", "хей", "привет", "слушай", "окей", "ок", "ok", "hey", "ну", "так"]);

function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^\p{L}\p{N}\s-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Варианты написания имени, которые выдаёт распознавание. */
export function wakeVariants(names: string[]): Set<string> {
  const out = new Set<string>();
  for (const raw of names) {
    const n = norm(raw);
    if (!n) continue;
    out.add(n);
    out.add(n.replace(/й/g, "и"));
    if (n === "ая") ["айя", "aya", "ая-ая", "аюшка", "аечка"].forEach((v) => out.add(v));
  }
  return out;
}

/** Есть ли обращение по имени в начале или в конце фразы.
 *  «Ая, открой калькулятор» → {matched, rest: "открой калькулятор"}.
 *  «А я вчера…» не считается: распознавание пишет два отдельных слова. */
export function matchWake(text: string, names: string[]): { matched: boolean; rest: string } {
  const variants = wakeVariants(names);
  const words = text.trim().split(/\s+/).filter(Boolean);
  const normed = words.map(norm);
  if (!words.length || !variants.size) return { matched: false, rest: "" };
  const strip = (arr: string[]) => arr.join(" ").replace(/^[\s,.!?:;—-]+/, "").trim();
  // начало: «[эй] Ая, …»
  for (let i = 0; i < Math.min(2, normed.length); i++) {
    if (variants.has(normed[i]) && normed.slice(0, i).every((w) => FILLERS.has(w))) {
      return { matched: true, rest: strip(words.slice(i + 1)) };
    }
    // имя из двух слов: «Мику чан»
    if (i + 1 < normed.length && variants.has(`${normed[i]} ${normed[i + 1]}`) && normed.slice(0, i).every((w) => FILLERS.has(w))) {
      return { matched: true, rest: strip(words.slice(i + 2)) };
    }
  }
  // конец: «…, Ая»
  const last = normed[normed.length - 1];
  if (normed.length > 1 && variants.has(last)) {
    return { matched: true, rest: words.slice(0, -1).join(" ").replace(/[\s,.!?:;—-]+$/, "").trim() };
  }
  return { matched: false, rest: "" };
}

/** Имена для обращения: имя персоны + свои варианты через запятую. */
export function wakeNames(personaName: string, extra: string): string[] {
  return [personaName, ...extra.split(",")].map((s) => s.trim()).filter(Boolean);
}

/** Ая свёрнута или в трее: фразу по клавише («hold») слушаем и отвечаем голосом,
    а фоновую речь (авто, свободные руки, перебивание) — выбрасываем. */
export function dropWhenHidden(origin: string, hidden: boolean): boolean {
  return hidden && origin !== "hold";
}
