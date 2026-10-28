/* Собственный плеер Аи (Яндекс Музыка по токену): очередь в ядре, звук здесь.
 *
 * Ядро меняет состояние (seq) — окно узнаёт из ответа чата, из /api/ui и
 * частым опросом, пока что-то играет. Трек доиграл — говорим ядру «ended»,
 * оно отдаёт следующий. Пока Ая говорит или слушает — музыка тише. */
import { musicStep, nowPlayingLine, rampVolume, type MusicSnap } from "./music-logic";

export interface MusicPlayerOpts {
  coreUrl: string;
  onTrack?: (line: string) => void;
}

export class AyaMusic {
  private audio = new Audio();
  private seq = -1;
  private itemId = "";
  private playing = false;
  private ducked = false;
  private vol = 1;
  private errors = 0;
  private pollTimer = 0;
  private rampTimer = 0;

  constructor(private opts: MusicPlayerOpts) {
    this.audio.preload = "auto";
    this.audio.crossOrigin = "anonymous";
    this.audio.addEventListener("ended", () => void this.report("ended"));
    this.audio.addEventListener("error", () => {
      if (!this.itemId) return;
      this.errors++;
      // пару битых треков пропускаем, дальше — стоп, чтобы не крутиться вечно
      void this.report(this.errors <= 3 ? "error" : "stop");
    });
    this.audio.addEventListener("playing", () => {
      this.errors = 0;
    });
  }

  get active(): boolean {
    return this.playing;
  }

  /** Новое состояние из ядра. */
  apply(snap: unknown) {
    const s = snap as MusicSnap | null;
    const step = musicStep({ seq: this.seq, itemId: this.itemId, playing: this.playing }, s);
    if (s && typeof s.seq === "number") this.seq = s.seq;
    if (step === "start" && s?.current) {
      this.itemId = s.current.id;
      this.audio.src = `${this.opts.coreUrl}/api/music/stream/${encodeURIComponent(s.current.id)}`;
      this.audio.volume = this.target();
      this.vol = this.audio.volume;
      this.playing = true;
      void this.audio.play().catch(() => {});
      this.opts.onTrack?.(nowPlayingLine(s.current));
    } else if (step === "pause") {
      this.audio.pause();
      this.playing = false;
    } else if (step === "resume") {
      this.playing = true;
      void this.audio.play().catch(() => {});
    } else if (step === "stop") {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
      this.itemId = "";
      this.playing = false;
    }
    this.schedulePoll(s);
  }

  /** Ая говорит или слушает — музыку тише (плавно). */
  duck(on: boolean) {
    if (this.ducked === on) return;
    this.ducked = on;
    window.clearInterval(this.rampTimer);
    this.rampTimer = window.setInterval(() => {
      this.vol = rampVolume(this.vol, this.target());
      this.audio.volume = this.vol;
      if (this.vol === this.target()) window.clearInterval(this.rampTimer);
    }, 30);
  }

  private target(): number {
    return this.ducked ? 0.22 : 1;
  }

  private async report(cmd: string) {
    try {
      const r = await fetch(`${this.opts.coreUrl}/api/music/player`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd }),
      });
      this.apply(await r.json());
    } catch {
      /* ядро недоступно — подождём опроса */
    }
  }

  /** Пока что-то играет или на паузе — опрашиваем часто (команды из Telegram и т.п.). */
  private schedulePoll(s: MusicSnap | null) {
    window.clearTimeout(this.pollTimer);
    const live = s && (s.status === "playing" || s.status === "paused");
    if (!live) return;
    this.pollTimer = window.setTimeout(async () => {
      try {
        this.apply(await (await fetch(`${this.opts.coreUrl}/api/music/player`)).json());
      } catch {
        this.schedulePoll(s);
      }
    }, 1200);
  }
}
