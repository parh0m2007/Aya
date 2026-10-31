/* Собственный плеер Аи (Яндекс Музыка по токену): очередь в ядре, звук здесь.
 *
 * Ядро меняет состояние (seq) — окно узнаёт из ответа чата, из /api/ui и
 * частым опросом, пока что-то играет. Трек доиграл — говорим ядру «ended»,
 * оно отдаёт следующий. Пока Ая говорит или слушает — музыка тише.
 * Громкость трека тоже живёт в ядре: её меняют и кнопки плеера, и голос
 * («сделай музыку тише»). */
import { musicStep, nowPlayingLine, playerGain, rampVolume, type MusicSnap } from "./music-logic";

export interface MusicPlayerOpts {
  coreUrl: string;
  onTrack?: (line: string) => void;
  /** любое новое состояние из ядра — для плеера в окне */
  onState?: (snap: MusicSnap) => void;
}

export class AyaMusic {
  private audio = new Audio();
  private seq = -1;
  private itemId = "";
  private playing = false;
  private ducked = false;
  private gain = 1; // громкость трека из ядра, 0–1
  private vol = 1; // что сейчас стоит у <audio> (с учётом приглушения)
  private errors = 0;
  private pollTimer = 0;
  private rampTimer = 0;

  constructor(private opts: MusicPlayerOpts) {
    this.audio.preload = "auto";
    this.audio.crossOrigin = "anonymous";
    this.audio.addEventListener("ended", () => void this.send("ended"));
    this.audio.addEventListener("error", () => {
      if (!this.itemId) return;
      this.errors++;
      // пару битых треков пропускаем, дальше — стоп, чтобы не крутиться вечно
      void this.send(this.errors <= 3 ? "error" : "stop");
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
    if (s && typeof s.seq === "number") this.setGain(playerGain(s));
    if (step === "start" && s?.current) {
      this.itemId = s.current.id;
      this.audio.src = `${this.opts.coreUrl}/api/music/stream/${encodeURIComponent(s.current.id)}`;
      window.clearInterval(this.rampTimer);
      this.vol = this.target();
      this.audio.volume = this.vol;
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
    if (s && typeof s.seq === "number") this.opts.onState?.(s);
    this.schedulePoll(s);
  }

  /** Ая говорит или слушает — музыку тише (плавно). */
  duck(on: boolean) {
    if (this.ducked === on) return;
    this.ducked = on;
    this.ramp();
  }

  /** Команда плееру (кнопки в окне, «трек доиграл»): ядро отвечает новым состоянием. */
  async send(cmd: string): Promise<boolean> {
    try {
      const r = await fetch(`${this.opts.coreUrl}/api/music/player`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd }),
      });
      this.apply(await r.json());
      return true;
    } catch {
      return false; // ядро недоступно — подождём опроса
    }
  }

  private setGain(g: number) {
    if (Math.abs(g - this.gain) < 1e-3) return;
    this.gain = g;
    this.ramp();
  }

  private target(): number {
    return this.gain * (this.ducked ? 0.22 : 1);
  }

  private ramp() {
    window.clearInterval(this.rampTimer);
    this.rampTimer = window.setInterval(() => {
      const goal = this.target();
      this.vol = rampVolume(this.vol, goal);
      this.audio.volume = Math.max(0, Math.min(1, this.vol));
      if (this.vol === goal) window.clearInterval(this.rampTimer);
    }, 30);
  }

  /** Пока что-то играет или на паузе — опрашиваем часто (команды из Telegram, голосом и т.п.). */
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
