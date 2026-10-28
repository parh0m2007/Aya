import { CORE_URL } from "./persona";

/* Режимы голосового ввода главного окна. Режим хранится в ядре (/api/config voice_mode),
   менять — во вкладке «Настройки» веб-консоли. */

export type VoiceMode = "button" | "auto" | "hotkey";

export function currentVoiceMode(): VoiceMode {
  return (localStorage.getItem("ayaVoice") as VoiceMode) || "button";
}

let lastApplied: VoiceMode | null = null;
let voiceChangeHandler: (mode: VoiceMode) => void = () => {};

export async function syncVoiceMode(force = false): Promise<void> {
  try {
    const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as { voice_mode?: string };
    const m = (["button", "auto", "hotkey"] as const).includes(cfg.voice_mode as VoiceMode)
      ? (cfg.voice_mode as VoiceMode)
      : "button";
    if (force || m !== lastApplied) {
      lastApplied = m;
      localStorage.setItem("ayaVoice", m);
      voiceChangeHandler(m);
    }
  } catch {
    /* ядро недоступно — режим прежний */
  }
}

/* То же, что syncVoiceMode, но режим уже пришёл вместе с общим опросом /api/ui —
   без отдельного запроса к ядру. */
export function applyVoiceModeValue(value: unknown, force = false): void {
  const m = (["button", "auto", "hotkey"] as const).includes(value as VoiceMode) ? (value as VoiceMode) : "button";
  if (force || m !== lastApplied) {
    lastApplied = m;
    try {
      localStorage.setItem("ayaVoice", m);
    } catch {
      /* хранилище недоступно */
    }
    voiceChangeHandler(m);
  }
}

export function initVoice(onChange: (mode: VoiceMode) => void) {
  voiceChangeHandler = onChange;
}

/* Что делать с глобальным хоткеем Ctrl+Space при применении режима.
   Повторный вызов с тем же режимом НЕ должен ничего снимать: раньше applyVoiceMode
   каждый раз снимал регистрацию, а восстанавливал её только при hotkeyBound=false —
   из-за этого клавиша «залипала» до перезапуска приложения. */
export function hotkeyAction(mode: VoiceMode, bound: boolean): "register" | "unregister" | "none" {
  if (mode === "hotkey") return bound ? "none" : "register";
  return bound ? "unregister" : "none";
}

/* Гейт удержания микрофона. Отпускание (Released/pointerup) может прийти раньше,
   чем откроется поток getUserMedia — тогда отложенный старт записи нужно отменить:
   иначе MediaRecorder запустится «призраком» и в hotkey-режиме (без VAD) уже не остановится.
   begin() выдаёт токен; end() делает все выданные токены неактуальными. */
export class HoldGate {
  private gen = 0;
  private held = false;

  begin(): number {
    this.held = true;
    return ++this.gen;
  }

  end(): void {
    this.held = false;
    this.gen++;
  }

  isCurrent(token: number): boolean {
    return this.held && token === this.gen;
  }
}
