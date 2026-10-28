/* Поле «нажми сочетание»: ловит нажатие, просит главное окно перепривязать клавишу
   и показывает, получилось ли (занятое сочетание не ломает старое). */
import { accelFromEvent, accelLabel } from "../hotkey";
import { el, fetchJson, postJson } from "./ui";

export type HotkeyKey = "hotkey_talk" | "hotkey_toggle";

const DEFAULTS: Record<HotkeyKey, string> = { hotkey_talk: "Control+Space", hotkey_toggle: "Alt+Shift+A" };

type Result = { key: HotkeyKey; accel: string; ok: boolean; error?: string };

/** Просим главное окно применить сочетание; null — окно не ответило за 2 с. */
async function applyInMain(key: HotkeyKey, accel: string): Promise<Result | null> {
  try {
    const { emitTo, listen } = await import("@tauri-apps/api/event");
    return await new Promise<Result | null>((resolve) => {
      let unlisten: (() => void) | null = null;
      const timer = window.setTimeout(() => {
        unlisten?.();
        resolve(null);
      }, 2000);
      void listen<Result>("aya-hotkeys-result", (ev) => {
        if (ev.payload.key !== key || ev.payload.accel !== accel) return;
        window.clearTimeout(timer);
        unlisten?.();
        resolve(ev.payload);
      }).then((u) => {
        unlisten = u;
        void emitTo("main", "aya-hotkeys", { key, accel });
      });
    });
  } catch {
    return null; // не в приложении (консоль в браузере)
  }
}

export function hotkeyField(key: HotkeyKey, label: string): HTMLElement {
  const row = el("div", "set-item hotkey-row");
  const t = el("span", "grow", label);
  const btn = el("button", "top-btn hotkey-btn") as HTMLButtonElement;
  const note = el("small", "dim block hotkey-note");
  t.append(note);
  let current = DEFAULTS[key];
  let waiting = false;

  const show = () => {
    btn.textContent = accelLabel(current);
    btn.classList.remove("listening");
  };
  void fetchJson<Record<string, string>>("/api/config")
    .then((c) => {
      current = c[key] || DEFAULTS[key];
      show();
    })
    .catch(show);
  show();

  const stop = () => {
    waiting = false;
    window.removeEventListener("keydown", onKey, true);
    show();
  };

  const onKey = async (e: KeyboardEvent) => {
    if (!waiting) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      note.textContent = "";
      stop();
      return;
    }
    const accel = accelFromEvent(e);
    if (!accel) {
      // модификатор без клавиши — ждём дальше; одиночная буква — подсказываем
      if (!/^(Control|Shift|Alt|Meta|OS)/.test(e.code)) note.textContent = "Так нельзя — добавь Ctrl, Alt или Shift";
      return;
    }
    stop();
    btn.textContent = accelLabel(accel);
    note.textContent = "Проверяю…";
    const r = await applyInMain(key, accel);
    if (r === null) {
      await postJson("/api/config", { [key]: accel }).catch(() => {});
      current = accel;
      note.textContent = "Сохранено";
    } else if (r.ok) {
      current = accel;
      note.textContent = "Сохранено";
    } else {
      note.textContent = r.error === "busy" ? "Это сочетание занято — выбери другое" : "Так нельзя — добавь Ctrl, Alt или Shift";
    }
    show();
  };

  btn.addEventListener("click", () => {
    if (waiting) return stop();
    waiting = true;
    btn.textContent = "Нажми сочетание…";
    btn.classList.add("listening");
    note.textContent = "Esc — отмена";
    window.addEventListener("keydown", onKey, true);
  });

  row.append(t, btn);
  return row;
}

/** Карточка «Клавиши» для раздела «Голос». */
export function hotkeysCard(): HTMLElement {
  const card = el("div", "card");
  card.append(
    el("h3", "sec-title", "Клавиши"),
    hotkeyField("hotkey_talk", "Зажми и говори"),
    hotkeyField("hotkey_toggle", "Показать или спрятать Аю"),
  );
  return card;
}
