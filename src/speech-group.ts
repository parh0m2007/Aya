/* Группировка реплик Аи для озвучки. Первый кусок короче — голос стартует раньше;
   дальше куски до 230 символов, чтобы не плодить лишние запросы к TTS. */
export const FIRST_SPEECH_CHUNK = 110;
export const SPEECH_CHUNK = 230;

export function speechChunkLimit(first: boolean): number {
  return first ? FIRST_SPEECH_CHUNK : SPEECH_CHUNK;
}

/* prev — накопленный кусок, piece — новая фраза. Если вместе влезают в limit —
   склеиваем; иначе flush = prev (пора озвучивать), buf = piece. */
export function groupSpeech(
  prev: string,
  piece: string,
  limit: number,
): { buf: string; flush: string | null } {
  if (prev && prev.length + piece.length + 1 <= limit) {
    return { buf: `${prev} ${piece}`, flush: null };
  }
  return { buf: piece, flush: prev || null };
}

/* Первая фраза уходит в озвучку сразу, как только набралось EARLY_FIRST символов:
   не ждём, пока придёт вторая фраза (иначе короткие ответы звучали только
   после полной генерации). */
export const EARLY_FIRST = 24;

export function shouldFlushEarly(buf: string, firstQueued: boolean): boolean {
  return !firstQueued && buf.trim().length >= EARLY_FIRST;
}
