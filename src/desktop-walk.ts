/* «Она живёт на рабочем столе»: иногда сама перемещается — вдоль панели
 * задач или на верхний край активного окна. Включается переключателем.
 *
 * Окно Аи двигается плавно (setPosition ~30 раз в секунду), аватар в это
 * время покачивается. Если пользователь сам перетащил её — 10 минут не гуляет.
 */
import { CORE_URL } from "./persona";
import { pickWalkTarget, type Rect } from "./walk-logic";

export interface WalkHooks {
  enabled: () => boolean;
  canMove: () => boolean; // нет разговора, панели, записи, нажатия
  onWalk: (dir: number) => void; // -1 влево, 1 вправо, 0 стоп
  onLand: (kind: "floor" | "window") => void;
}

let selfMoving = false;
let pausedUntil = 0;
let nextAt = 0;
let perch: Rect | null = null;

function rand(a: number, b: number) {
  return a + Math.random() * (b - a);
}

async function foreground(): Promise<(Rect & { maximized: boolean }) | null> {
  try {
    const r = await fetch(`${CORE_URL}/api/desktop/foreground`);
    return ((await r.json()) as { window: (Rect & { maximized: boolean }) | null }).window;
  } catch {
    return null;
  }
}

export async function initDesktopWalk(h: WalkHooks): Promise<void> {
  let api: typeof import("@tauri-apps/api/window");
  try {
    api = await import("@tauri-apps/api/window");
    api.getCurrentWindow();
  } catch {
    return; // вне приложения окно не двигаем
  }
  const win = api.getCurrentWindow();
  nextAt = Date.now() + rand(60_000, 120_000);

  // пользователь перетащил её сам — пусть постоит там, куда поставили
  await win.onMoved(() => {
    if (!selfMoving) {
      pausedUntil = Date.now() + 10 * 60_000;
      perch = null;
    }
  });

  const glide = async (to: { x: number; y: number }, speed: number) => {
    const from = await win.outerPosition();
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 4) return;
    const ms = Math.max(400, (dist / speed) * 1000);
    h.onWalk(Math.sign(dx) || 1);
    selfMoving = true;
    const t0 = performance.now();
    try {
      for (;;) {
        const k = Math.min(1, (performance.now() - t0) / ms);
        const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; // мягкий старт и стоп
        await win.setPosition(new api.PhysicalPosition(Math.round(from.x + dx * e), Math.round(from.y + dy * e)));
        if (k >= 1) break;
        if (!h.enabled()) break;
        await new Promise((r) => setTimeout(r, 33));
      }
    } finally {
      // onMoved приходит с задержкой — не принимаем свои шаги за перетаскивание
      window.setTimeout(() => (selfMoving = false), 300);
      h.onWalk(0);
    }
  };

  const tick = async () => {
    if (!h.enabled() || document.visibilityState === "hidden") return;
    const now = Date.now();
    // сидит на окне: окно уехало, закрылось или свернулось — спрыгивает вниз
    if (perch) {
      const fg = await foreground();
      const moved = !fg || Math.abs(fg.x - perch.x) > 6 || Math.abs(fg.y - perch.y) > 6 || Math.abs(fg.w - perch.w) > 6;
      if (moved && h.canMove()) {
        perch = null;
        await walkTo("floor", 900);
      }
      return;
    }
    if (now < nextAt || now < pausedUntil || !h.canMove()) return;
    nextAt = now + rand(3 * 60_000, 7 * 60_000);
    await walkTo(Math.random() < 0.55 ? "window" : "floor", 180);
  };

  const walkTo = async (want: "window" | "floor", speed: number) => {
    const mon = await api.currentMonitor();
    if (!mon) return;
    const size = await win.outerSize();
    const pos = await win.outerPosition();
    const fg = want === "window" ? await foreground() : null;
    const work = { x: mon.workArea.position.x, y: mon.workArea.position.y, w: mon.workArea.size.width, h: mon.workArea.size.height };
    const target = pickWalkTarget({
      work,
      self: { x: pos.x, y: pos.y, w: size.width, h: size.height },
      fg: fg && !fg.maximized ? fg : null,
      feet: Math.round(8 * mon.scaleFactor),
      rnd: Math.random(),
    });
    await glide(target, speed * mon.scaleFactor);
    perch = target.kind === "window" && fg ? { x: fg.x, y: fg.y, w: fg.w, h: fg.h } : null;
    h.onLand(target.kind);
  };

  window.setInterval(() => void tick(), 3000);
}
