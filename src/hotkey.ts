/* Сочетания клавиш: разбор нажатия в формат Tauri, подпись для человека и
   безопасная перепривязка (старое не снимаем, пока новое не заработало). */

export type KeyLike = Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "altKey" | "shiftKey" | "metaKey">;

const MODIFIER_CODES = /^(Control|Shift|Alt|Meta|OS)(Left|Right)?$/;
// одиночные клавиши, которые не мешают набору текста
const SOLO = /^(F([1-9]|1\d|2[0-4])|Pause|ScrollLock|Insert)$/;

/** Физическая клавиша (по code, а не по раскладке) → имя для Tauri. */
function keyName(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^Numpad\d$/.test(code)) return code;
  if (/^F([1-9]|1\d|2[0-4])$/.test(code)) return code;
  const named: Record<string, string> = {
    Space: "Space", Enter: "Enter", Tab: "Tab", Backquote: "Backquote", Minus: "Minus", Equal: "Equal",
    BracketLeft: "BracketLeft", BracketRight: "BracketRight", Backslash: "Backslash", Semicolon: "Semicolon",
    Quote: "Quote", Comma: "Comma", Period: "Period", Slash: "Slash", Insert: "Insert", Delete: "Delete",
    Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
    ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", Pause: "Pause", ScrollLock: "ScrollLock",
  };
  return named[code] ?? null;
}

/** Нажатие → «Control+Shift+Space» / «F9»; null — только модификатор или одиночная буква. */
export function accelFromEvent(e: KeyLike): string | null {
  if (MODIFIER_CODES.test(e.code)) return null;
  const key = keyName(e.code);
  if (!key) return null;
  const mods = [e.ctrlKey && "Control", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Super"].filter(Boolean) as string[];
  if (!mods.length && !SOLO.test(key)) return null; // одна буква сломала бы набор текста
  return [...mods, key].join("+");
}

/** «Control+Shift+Space» → «Ctrl+Shift+Space». */
export function accelLabel(accel: string): string {
  return accel
    .split("+")
    .map((p) => ({ Control: "Ctrl", Super: "Win", CommandOrControl: "Ctrl" })[p] ?? p)
    .join("+");
}

export interface ShortcutApi {
  register(accel: string, handler: (e: { state: "Pressed" | "Released" }) => void): Promise<void>;
  unregister(accel: string): Promise<void>;
}

export type RebindResult = { ok: boolean; error?: "busy" | "invalid" };

/** Сначала регистрируем новое; не вышло — старое остаётся работать. */
export async function rebind(
  gs: Pick<ShortcutApi, "unregister"> & { register(accel: string, handler: never): Promise<void> },
  oldAccel: string,
  newAccel: string,
  handler: (e: { state: "Pressed" | "Released" }) => void,
): Promise<RebindResult> {
  if (!newAccel) return { ok: false, error: "invalid" };
  if (newAccel === oldAccel) return { ok: true };
  try {
    await gs.register(newAccel, handler as never);
  } catch {
    return { ok: false, error: "busy" };
  }
  if (oldAccel) await gs.unregister(oldAccel).catch(() => {});
  return { ok: true };
}
