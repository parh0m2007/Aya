/* Игровой режим в окне Аи: ядро замечает полноэкранную игру (флаг game в /api/ui),
   окно перестаёт быть «поверх всех» (игра закрывает Аю, и Windows выводит её
   кадры напрямую), не рисует аватар и не слушает микрофон постоянно — говорить
   можно только по клавише, ответ звучит голосом. */
import type { VoiceMode } from "./voice-ctl";

/** Режим голоса с учётом игры: в игре — только клавиша. */
export function gameVoiceMode(mode: VoiceMode, game: boolean): VoiceMode {
  return game ? "hotkey" : mode;
}

/** Слушает ли микрофон постоянно («по голосу» или «Ая, …»). В игре — нет. */
export function listensAlways(mode: VoiceMode, wake: boolean, game: boolean): boolean {
  return !game && (mode === "auto" || wake);
}

/** Рисовать ли аватар. В игре окно видно формально (оно лишь под игрой) — но не рисуем. */
export function rendersNow(game: boolean, windowHidden: boolean): boolean {
  return !game && !windowHidden;
}
