import { followCursor, modelHit } from "./avatar";

/* Сквозные клики и взгляд за курсором.
 *
 * Окно Аи прозрачное 420×780, но раньше оно перехватывало клики по всей
 * площади — пустые места над рабочим столом были «мёртвыми». Теперь
 * ~15 раз в секунду берём глобальную позицию курсора и, если под ним нет
 * ни силуэта Аи, ни элементов интерфейса, просим ОС пропускать мышь сквозь
 * окно. Та же позиция курсора управляет взглядом — она смотрит на курсор
 * по всему экрану, а не только внутри своего окна.
 */

const TICK_MS = 66;

function isSolidElement(el: Element | null): boolean {
  if (!el) return false;
  if (el === document.body || el === document.documentElement) return false;
  if (el.id === "stage" || el.tagName === "CANVAS") return false;
  return true;
}

let pressing = false;

export async function initHoverPass(enabled: () => boolean, paused: () => boolean = () => false): Promise<void> {
  window.addEventListener("pointerdown", () => (pressing = true), true);
  window.addEventListener("pointerup", () => (pressing = false), true);
  window.addEventListener("blur", () => (pressing = false));
  // после перетаскивания окна ОС «съедает» pointerup — сбрасываем по первому движению без кнопок
  window.addEventListener("pointermove", (e) => {
    if (pressing && e.buttons === 0) pressing = false;
  });

  let api: typeof import("@tauri-apps/api/window");
  try {
    api = await import("@tauri-apps/api/window");
    api.getCurrentWindow(); // вне Tauri бросит
  } catch {
    // обычный браузер (vite dev): только взгляд внутри окна
    window.addEventListener("pointermove", (e) => followCursor(e.clientX, e.clientY, true));
    return;
  }

  const win = api.getCurrentWindow();
  let pos = { x: 0, y: 0 };
  let sf = window.devicePixelRatio || 1;
  try {
    const p = await win.outerPosition();
    pos = { x: p.x, y: p.y };
    sf = await win.scaleFactor();
    await win.onMoved(({ payload }) => (pos = { x: payload.x, y: payload.y }));
    await win.onScaleChanged(({ payload }) => (sf = payload.scaleFactor));
  } catch {
    /* позиция окна недоступна — посчитаем от нуля */
  }

  let ignoring = false;
  let broken = false;
  let lastX = NaN;
  let lastY = NaN;
  let busy = false;

  const setIgnore = async (v: boolean) => {
    if (v === ignoring) return;
    ignoring = v;
    try {
      await win.setIgnoreCursorEvents(v);
    } catch {
      // нет разрешения или платформа не умеет — больше не пробуем, окно остаётся кликабельным
      broken = true;
      ignoring = false;
    }
  };

  const tick = async () => {
    if (busy || document.visibilityState === "hidden" || paused()) return; // в игре курсор не опрашиваем
    busy = true;
    try {
      const cur = await api.cursorPosition();
      const x = (cur.x - pos.x) / sf;
      const y = (cur.y - pos.y) / sf;
      const moved = x !== lastX || y !== lastY;
      lastX = x;
      lastY = y;
      followCursor(x, y, moved);
      if (broken) return;
      if (!enabled()) {
        await setIgnore(false);
        return;
      }
      if (pressing) return; // во время нажатия/перетаскивания не переключаем
      const inside = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight;
      if (!inside) return; // вне окна состояние не важно
      const solid = isSolidElement(document.elementFromPoint(x, y)) || !!modelHit(x, y);
      await setIgnore(!solid);
    } catch {
      /* курсор недоступен (экран блокировки и т.п.) */
    } finally {
      busy = false;
    }
  };

  window.setInterval(() => void tick(), TICK_MS);
  // на всякий случай: при уходе со страницы окно снова принимает мышь
  window.addEventListener("beforeunload", () => void win.setIgnoreCursorEvents(false).catch(() => {}));
}
